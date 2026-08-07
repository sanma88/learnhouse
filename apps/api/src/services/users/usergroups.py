from datetime import datetime
import logging
from typing import Literal
from uuid import uuid4
from fastapi import HTTPException, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from src.security.features_utils.usage import (
    check_limits_with_usage,
    increase_feature_usage,
    decrease_feature_usage,
)
from src.security.rbac.rbac import (
    authorization_verify_based_on_roles_and_authorship,
    authorization_verify_based_on_roles_and_authorship_or_api_token,
    authorization_verify_if_user_is_anon,
)
from src.security.org_auth import (
    is_org_admin,
    require_org_role_permission,
    require_org_membership,
)
from src.security.rbac.config import get_resource_config
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.user_organizations import UserOrganization
from src.db.organizations import Organization
from src.db.usergroups import UserGroup, UserGroupCreate, UserGroupRead, UserGroupUpdate
from src.db.users import AnonymousUser, APITokenUser, InternalUser, PublicUser, User, UserRead
from src.services.webhooks.dispatch import dispatch_webhooks
from src.services.security.rate_limiting import enforce_batch_size_limit


# HI-HA: ─── cross-tenant scoping helpers ──────────────────────────────────────
#
# This deployment packs the trainings of ~25 *different* clients into ONE
# organization (multi-org is a paid Enterprise feature), each client isolated by
# its own usergroup. Content access is already correctly gated by
# `check_resource_access`, but the usergroup *metadata* endpoints only checked
# "is a member of the org" / "role has usergroups.action_read", which every
# Read-Only Learner satisfies. That let any trainee enumerate the client list
# (group names), the roster of other clients' groups, and the course↔group
# mapping. The two helpers below let each read scope itself to what the caller
# legitimately owns, while org admins/maintainers keep full visibility because
# they are the ones who administer the groups.


async def _caller_is_org_admin(
    current_user: "PublicUser | AnonymousUser | APITokenUser | InternalUser",
    org_id: int,
    db_session: AsyncSession,
) -> bool:
    """HI-HA: True when the caller may see the whole org's usergroups.

    Delegates to `src.security.org_auth.is_org_admin`, the project's existing
    admin seam (it already folds in the superadmin bypass), rather than
    re-deriving roles here. InternalUser is the server-side service principal
    and is unconditionally trusted; API tokens resolve to their creator via
    `resolve_acting_user_id`, as everywhere else in this module.
    """
    from src.security.auth import resolve_acting_user_id

    if isinstance(current_user, InternalUser):
        return True

    user_id = resolve_acting_user_id(current_user)
    if not user_id:
        return False

    return await is_org_admin(user_id, org_id, db_session)


async def _usergroup_ids_of_caller(
    current_user: "PublicUser | AnonymousUser | APITokenUser | InternalUser",
    db_session: AsyncSession,
) -> set[int]:
    """HI-HA: the ids of the usergroups the caller actually belongs to.

    Used to filter list endpoints down to the caller's own group(s) instead of
    returning the whole organization.
    """
    from src.security.auth import resolve_acting_user_id

    user_id = resolve_acting_user_id(current_user)
    if not user_id:
        return set()

    statement = select(UserGroupUser.usergroup_id).where(
        UserGroupUser.user_id == user_id
    )
    return set((await db_session.execute(statement)).scalars().all())


async def _validate_resource_exists_and_belongs_to_org(
    resource_uuid: str,
    org_id: int,
    db_session: AsyncSession,
) -> bool:
    """
    Validate that a resource exists and belongs to the specified organization.

    Args:
        resource_uuid: UUID of the resource (course_xxx, podcast_xxx, community_xxx)
        org_id: Organization ID the resource should belong to
        db_session: Database session

    Returns:
        True if resource exists and belongs to org

    Raises:
        HTTPException: If resource doesn't exist or doesn't belong to org
    """
    config = get_resource_config(resource_uuid)
    if not config:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown resource type for UUID: {resource_uuid}",
        )

    # Import the appropriate model based on resource type
    resource = None

    if config.resource_type == "courses":
        from src.db.courses.courses import Course
        statement = select(Course).where(Course.course_uuid == resource_uuid)
        resource = (await db_session.execute(statement)).scalars().first()
    elif config.resource_type == "podcasts":
        from src.db.podcasts.podcasts import Podcast
        statement = select(Podcast).where(Podcast.podcast_uuid == resource_uuid)
        resource = (await db_session.execute(statement)).scalars().first()
    elif config.resource_type == "communities":
        from src.db.communities.communities import Community
        statement = select(Community).where(Community.community_uuid == resource_uuid)
        resource = (await db_session.execute(statement)).scalars().first()
    elif config.resource_type == "folders":
        from src.db.folders.folders import Folder
        statement = select(Folder).where(Folder.folder_uuid == resource_uuid)
        resource = (await db_session.execute(statement)).scalars().first()
    elif config.resource_type == "media":
        from src.db.media.media import Media
        statement = select(Media).where(Media.media_uuid == resource_uuid)
        resource = (await db_session.execute(statement)).scalars().first()
    elif config.resource_type == "boards":
        from src.db.boards import Board
        statement = select(Board).where(Board.board_uuid == resource_uuid)
        resource = (await db_session.execute(statement)).scalars().first()
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Resource type '{config.resource_type}' is not supported for UserGroup linking",
        )

    if not resource:
        raise HTTPException(
            status_code=404,
            detail=f"Resource {resource_uuid} not found",
        )

    # Verify resource belongs to the same organization
    # All supported resource types (courses, podcasts, communities, folders, media) have org_id
    if not hasattr(resource, 'org_id'):
        raise HTTPException(
            status_code=500,
            detail=f"Resource {resource_uuid} does not have organization association",
        )

    if resource.org_id != org_id:
        raise HTTPException(
            status_code=403,
            detail=f"Resource {resource_uuid} does not belong to this organization",
        )

    return True


async def create_usergroup(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    usergroup_create: UserGroupCreate,
) -> UserGroupRead:

    from src.security.auth import resolve_acting_user_id

    usergroup = UserGroup.model_validate(usergroup_create)

    # SECURITY: enforce the API-token org boundary. The target org here comes
    # from the request body (usergroup_create.org_id), which neither the global
    # path/query org-boundary net (auth._verify_api_token_org_boundary) nor the
    # RBAC element-org lookup can see — the RBAC check below authorizes against
    # the placeholder "usergroup_X", whose org resolves to None so the boundary
    # check is skipped. Without this, a token whose creator also belongs to
    # another org could create a usergroup in that other org. A token is bound
    # to exactly one org, so refuse any mismatch.
    if isinstance(current_user, APITokenUser) and usergroup_create.org_id != current_user.org_id:
        raise HTTPException(
            status_code=403,
            detail="API token cannot create resources outside its organization",
        )

    await require_org_membership(
        resolve_acting_user_id(current_user), usergroup_create.org_id, db_session
    )

    # RBAC check
    # HI-HA: pass the target org so `rbac_check` takes the
    # `require_org_role_permission("usergroups", "action_create")` branch
    # instead of the placeholder path. The placeholder path routes through
    # `authorization_verify_if_user_is_author`, which returns True for *any*
    # authenticated caller when action == "create" (see rbac.py), so the role's
    # `usergroups.action_create: false` was never consulted and a Read-Only
    # Learner could create groups. The org-scoped branch honours the rights
    # dict, so Admin/Maintainer (create=true) still pass and User/Instructor
    # (create=false) are refused — least privilege, no new mechanism.
    await rbac_check(
        request,
        usergroup_uuid="usergroup_X",
        current_user=current_user,
        action="create",
        db_session=db_session,
        org_id=usergroup_create.org_id,
    )

    # Check if Organization exists
    statement = select(Organization).where(Organization.id == usergroup_create.org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org or org.id is None:
        raise HTTPException(
            status_code=400,
            detail="Organization does not exist",
        )

    # Usage check — this is the usergroups limit, not courses. (Previously
    # keyed "courses", so the usergroups cap was never actually enforced.)
    await check_limits_with_usage("usergroups", org.id, db_session)

    # Complete the object
    usergroup.usergroup_uuid = f"usergroup_{uuid4()}"
    usergroup.creation_date = str(datetime.now())
    usergroup.update_date = str(datetime.now())

    # Save the object
    db_session.add(usergroup)
    await db_session.commit()
    await db_session.refresh(usergroup)

    # Feature usage
    await increase_feature_usage("usergroups", org.id, db_session)

    await dispatch_webhooks(
        event_name="usergroup_created",
        org_id=org.id,
        data={
            "usergroup_uuid": usergroup.usergroup_uuid,
            "name": usergroup.name,
        },
    )

    usergroup = UserGroupRead.model_validate(usergroup)

    return usergroup


async def read_usergroup_by_id(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    usergroup_id: int,
) -> UserGroupRead:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="read",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    # HI-HA: the RBAC check above only asserts "usergroups.action_read in this
    # org", which the default Read-Only Learner role grants. On a single-org,
    # multi-client install that let any trainee read back another client's
    # group by walking the numeric id (group name == client name). Non-admins
    # may only read a group they belong to.
    if not await _caller_is_org_admin(current_user, usergroup.org_id, db_session):
        if usergroup.id not in await _usergroup_ids_of_caller(current_user, db_session):
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this usergroup",
            )

    usergroup = UserGroupRead.model_validate(usergroup)

    return usergroup


async def get_users_linked_to_usergroup(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    usergroup_id: int,
) -> list[UserRead]:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="read",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    # HI-HA: listing the members of a group is an ADMINISTRATION action, not a
    # learner one — the frontend only calls it from the Dashboard. The RBAC
    # check above passes for the Read-Only Learner role (usergroups.action_read
    # is true for it), so any trainee could dump the roster of every other
    # client's group. Restrict to org admins/maintainers; a learner has no
    # legitimate need for the member list of *any* group, including their own,
    # so there is deliberately no "own group" exception here.
    if not await _caller_is_org_admin(current_user, usergroup.org_id, db_session):
        raise HTTPException(
            status_code=403,
            detail="Only organization administrators can list usergroup members",
        )

    # Batch fetch users linked to this usergroup in a single query
    statement = (
        select(User)
        .join(UserGroupUser, UserGroupUser.user_id == User.id)  # type: ignore
        .where(UserGroupUser.usergroup_id == usergroup_id)
    )
    users = (await db_session.execute(statement)).scalars().all()

    return [UserRead.model_validate(user) for user in users]


async def read_usergroups_by_org_id(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    org_id: int,
) -> list[UserGroupRead]:

    from src.security.auth import resolve_acting_user_id

    await require_org_membership(
        resolve_acting_user_id(current_user), org_id, db_session
    )

    statement = select(UserGroup).where(UserGroup.org_id == org_id).order_by(UserGroup.creation_date.desc())
    usergroups = (await db_session.execute(statement)).scalars().all()

    # RBAC check
    await rbac_check(
        request,
        usergroup_uuid="usergroup_X",
        current_user=current_user,
        action="read",
        db_session=db_session,
    )

    # HI-HA: org membership + `usergroups.action_read` are both satisfied by the
    # default Read-Only Learner role, so this endpoint used to return EVERY
    # group of the org. With one group per client, that is the operator's whole
    # customer list handed to any trainee. Narrow non-admins to their own
    # group(s) — the query result is filtered rather than refused so the
    # endpoint keeps working for any UI that legitimately asks "which groups am
    # I in". Admins/maintainers (who administer the groups) see all of them.
    if not await _caller_is_org_admin(current_user, org_id, db_session):
        caller_group_ids = await _usergroup_ids_of_caller(current_user, db_session)
        usergroups = [ug for ug in usergroups if ug.id in caller_group_ids]

    usergroups = [UserGroupRead.model_validate(usergroup) for usergroup in usergroups]

    return usergroups


async def get_usergroups_by_resource(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser | APITokenUser,
    resource_uuid: str,
) -> list[UserGroupRead]:

    statement = select(UserGroupResource).where(
        UserGroupResource.resource_uuid == resource_uuid
    )
    usergroup_resources = (await db_session.execute(statement)).scalars().all()

    # Authorize unconditionally, before the empty-result shortcut below, so an
    # absent/unlinked resource cannot be used as an unauthenticated existence
    # oracle (an anonymous caller must get 403, not an empty 200).
    await rbac_check(
        request,
        usergroup_uuid="usergroup_X",
        current_user=current_user,
        action="read",
        db_session=db_session,
    )

    if not usergroup_resources:
        return []

    from src.security.auth import resolve_acting_user_id

    target_org_id = usergroup_resources[0].org_id

    # SECURITY: enforce the API-token org boundary. The org is derived from the
    # resource here, not from an org_id path/query param, so the global boundary
    # net cannot see it, and the RBAC check above ran against the placeholder
    # "usergroup_X" (org None → boundary skipped). Refuse cross-org reads so a
    # token cannot enumerate another org's usergroups via a resource its creator
    # happens to be able to reach.
    if isinstance(current_user, APITokenUser) and target_org_id != current_user.org_id:
        raise HTTPException(
            status_code=403,
            detail="API token cannot access resources outside its organization",
        )

    await require_org_membership(
        resolve_acting_user_id(current_user), target_org_id, db_session
    )

    # Batch fetch all usergroups in a single query
    usergroup_ids = [ug.usergroup_id for ug in usergroup_resources]
    if not usergroup_ids:
        return []

    statement = select(UserGroup).where(UserGroup.id.in_(usergroup_ids))  # type: ignore
    usergroups = (await db_session.execute(statement)).scalars().all()

    # HI-HA: this endpoint reveals the resource↔group mapping. Given a course
    # UUID, any trainee could learn which client group owns it (and its name),
    # which also turns a guessed UUID into an existence + ownership oracle.
    # Non-admins only get back the groups they are themselves in, so the answer
    # can never describe another client. An empty list is the correct response
    # for a course that isn't theirs — it leaks nothing the caller didn't
    # already know.
    if not await _caller_is_org_admin(current_user, target_org_id, db_session):
        caller_group_ids = await _usergroup_ids_of_caller(current_user, db_session)
        usergroups = [ug for ug in usergroups if ug.id in caller_group_ids]

    return [UserGroupRead.model_validate(ug) for ug in usergroups]


async def get_resources_by_usergroup(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    usergroup_id: int,
) -> list[str]:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="read",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    # HI-HA: the mirror image of `get_usergroups_by_resource` — given a group id
    # it returns that group's course UUIDs. Observed leaking another client's
    # course UUID to a Read-Only Learner, so it is closed with the same rule:
    # non-admins may only inspect a group they belong to.
    if not await _caller_is_org_admin(current_user, usergroup.org_id, db_session):
        if usergroup.id not in await _usergroup_ids_of_caller(current_user, db_session):
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this usergroup",
            )

    statement = select(UserGroupResource).where(
        UserGroupResource.usergroup_id == usergroup_id
    )
    usergroup_resources = (await db_session.execute(statement)).scalars().all()

    return [ugr.resource_uuid for ugr in usergroup_resources]


async def update_usergroup_by_id(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    usergroup_id: int,
    usergroup_update: UserGroupUpdate,
) -> UserGroupRead:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="update",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    if usergroup_update.name is not None:
        usergroup.name = usergroup_update.name
    if usergroup_update.description is not None:
        usergroup.description = usergroup_update.description
    usergroup.update_date = str(datetime.now())

    db_session.add(usergroup)
    await db_session.commit()
    await db_session.refresh(usergroup)

    usergroup = UserGroupRead.model_validate(usergroup)

    return usergroup


async def delete_usergroup_by_id(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    usergroup_id: int,
) -> str:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="delete",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    # Feature usage — deleting a usergroup must DECREASE the counter, not increase it.
    await decrease_feature_usage("usergroups", usergroup.org_id, db_session)

    usergroup_uuid_val = usergroup.usergroup_uuid
    usergroup_name_val = usergroup.name
    usergroup_org_id = usergroup.org_id

    await db_session.delete(usergroup)
    await db_session.commit()

    await dispatch_webhooks(
        event_name="usergroup_deleted",
        org_id=usergroup_org_id,
        data={
            "usergroup_uuid": usergroup_uuid_val,
            "name": usergroup_name_val,
        },
    )

    return "UserGroup deleted successfully"


async def add_users_to_usergroup(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser | InternalUser,
    usergroup_id: int,
    user_ids: str,
) -> str:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="create",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    try:
        user_ids_array = [int(uid) for uid in user_ids.split(",") if uid.strip() != ""]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="user_ids must be a comma-separated list of integers",
        )

    enforce_batch_size_limit(len(user_ids_array), "users")

    for user_id in user_ids_array:
        statement = select(User).where(User.id == user_id)
        user = (await db_session.execute(statement)).scalars().first()

        # Check if User is already Linked to UserGroup
        statement = select(UserGroupUser).where(
            UserGroupUser.usergroup_id == usergroup_id,
            UserGroupUser.user_id == user_id,
        )
        usergroup_user = (await db_session.execute(statement)).scalars().first()

        if usergroup_user:
            logging.error(f"User with id {user_id} already exists in UserGroup")
            continue

        membership_statement = select(UserOrganization).where(
            UserOrganization.user_id == user_id,
            UserOrganization.org_id == usergroup.org_id,
        )
        user_org_membership = (
            await db_session.execute(membership_statement)
        ).scalars().first()

        if not user_org_membership:
            logging.error(
                f"User with id {user_id} is not a member of org {usergroup.org_id}; skipping"
            )
            continue

        if user:
            # Add user to UserGroup
            if user.id is not None:
                usergroup_obj = UserGroupUser(
                    usergroup_id=usergroup_id,
                    user_id=user.id,
                    org_id=usergroup.org_id,
                    creation_date=str(datetime.now()),
                    update_date=str(datetime.now()),
                )

                db_session.add(usergroup_obj)
        else:
            logging.error(f"User with id {user_id} not found")

    await db_session.commit()

    await dispatch_webhooks(
        event_name="usergroup_users_added",
        org_id=usergroup.org_id,
        data={
            "usergroup_id": usergroup_id,
            "usergroup_uuid": usergroup.usergroup_uuid,
            "user_ids": user_ids_array,
        },
    )

    return "Users added to UserGroup successfully"


async def remove_users_from_usergroup(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser | InternalUser,
    usergroup_id: int,
    user_ids: str,
) -> str:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="delete",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    try:
        user_ids_array = [int(uid) for uid in user_ids.split(",") if uid.strip() != ""]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="user_ids must be a comma-separated list of integers",
        )

    enforce_batch_size_limit(len(user_ids_array), "users")

    for user_id in user_ids_array:
        statement = select(UserGroupUser).where(
            UserGroupUser.user_id == user_id, UserGroupUser.usergroup_id == usergroup_id
        )
        usergroup_user = (await db_session.execute(statement)).scalars().first()

        if usergroup_user:
            await db_session.delete(usergroup_user)
        else:
            logging.error(f"User with id {user_id} not found in UserGroup")

    await db_session.commit()

    return "Users removed from UserGroup successfully"


async def add_resources_to_usergroup(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    usergroup_id: int,
    resources_uuids: str,
) -> str:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="create",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    resources_uuids_array = [r for r in resources_uuids.split(",") if r.strip() != ""]

    enforce_batch_size_limit(len(resources_uuids_array), "resources")

    for resource_uuid in resources_uuids_array:
        # Check if a link between UserGroup and Resource already exists
        statement = select(UserGroupResource).where(
            UserGroupResource.usergroup_id == usergroup_id,
            UserGroupResource.resource_uuid == resource_uuid,
        )
        usergroup_resource = (await db_session.execute(statement)).scalars().first()

        if usergroup_resource:
            logging.error(f"Resource {resource_uuid} already exists in UserGroup")
            continue

        # Validate that resource exists and belongs to this organization
        await _validate_resource_exists_and_belongs_to_org(
            resource_uuid, usergroup.org_id, db_session
        )

        usergroup_obj = UserGroupResource(
            usergroup_id=usergroup_id,
            resource_uuid=resource_uuid,
            org_id=usergroup.org_id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )

        db_session.add(usergroup_obj)

    await db_session.commit()

    await dispatch_webhooks(
        event_name="usergroup_resources_added",
        org_id=usergroup.org_id,
        data={
            "usergroup_id": usergroup_id,
            "usergroup_uuid": usergroup.usergroup_uuid,
            "resource_uuids": resources_uuids_array,
        },
    )

    return "Resources added to UserGroup successfully"


async def remove_resources_from_usergroup(
    request: Request,
    db_session: AsyncSession,
    current_user: PublicUser | AnonymousUser,
    usergroup_id: int,
    resources_uuids: str,
) -> str:

    statement = select(UserGroup).where(UserGroup.id == usergroup_id)
    usergroup = (await db_session.execute(statement)).scalars().first()

    if not usergroup:
        raise HTTPException(
            status_code=404,
            detail="UserGroup not found",
        )

    # RBAC check — scoped to the usergroup's org to prevent cross-org IDOR
    await rbac_check(
        request,
        usergroup_uuid=usergroup.usergroup_uuid,
        current_user=current_user,
        action="delete",
        db_session=db_session,
        org_id=usergroup.org_id,
    )

    resources_uuids_array = [r for r in resources_uuids.split(",") if r.strip() != ""]

    enforce_batch_size_limit(len(resources_uuids_array), "resources")

    for resource_uuid in resources_uuids_array:
        statement = select(UserGroupResource).where(
            UserGroupResource.resource_uuid == resource_uuid,
            UserGroupResource.usergroup_id == usergroup_id,
        )
        usergroup_resource = (await db_session.execute(statement)).scalars().first()

        if usergroup_resource:
            await db_session.delete(usergroup_resource)
        else:
            logging.error(f"resource with uuid {resource_uuid} not found in UserGroup")

    await db_session.commit()

    return "Resources removed from UserGroup successfully"


## 🔒 RBAC Utils ##


_ACTION_PERMISSION_MAP: dict[str, str] = {
    "create": "action_create",
    "read": "action_read",
    "update": "action_update",
    "delete": "action_delete",
}


async def rbac_check(
    request: Request,
    usergroup_uuid: str,
    current_user: PublicUser | AnonymousUser | InternalUser | APITokenUser,
    action: Literal["create", "read", "update", "delete"],
    db_session: AsyncSession,
    org_id: int | None = None,
):
    if isinstance(current_user, InternalUser):
        return True

    # Handle both regular users and API tokens
    if isinstance(current_user, APITokenUser):
        await authorization_verify_based_on_roles_and_authorship_or_api_token(
            request,
            current_user,
            action,
            usergroup_uuid,
            db_session,
        )
    else:
        await authorization_verify_if_user_is_anon(current_user.id)

        if org_id is not None:
            # Scope permission check to the usergroup's own org to prevent cross-org IDOR.
            await require_org_role_permission(
                current_user.id,
                org_id,
                db_session,
                "usergroups",
                _ACTION_PERMISSION_MAP[action],
            )
        else:
            await authorization_verify_based_on_roles_and_authorship(
                request,
                current_user.id,
                action,
                usergroup_uuid,
                db_session,
            )


## 🔒 RBAC Utils ##
