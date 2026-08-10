'use client'
import { default as React, type JSX } from 'react';
import Editor from './Editor'
import { updateActivity, getActivityState, getActivity } from '@services/courses/activities'
import { toast } from 'react-hot-toast'
import Toast from '@components/Objects/StyledElements/Toast/Toast'
import { OrgProvider } from '@components/Contexts/OrgContext'
import { useLHSession } from '@components/Contexts/LHSessionContext'
import { useTranslation } from 'react-i18next'
import { preloadComponentsForContent } from './editorPreload'
import { sanitizeTiptapContent } from './sanitizeContent'
import { useLHAnalytics, AnalyticsEvent } from '@services/analytics'

export interface ConflictInfo {
  hasConflict: boolean
  remoteVersion: number
  localVersion: number
  lastModifiedBy: string | null
  lastModifiedAt: string | null
}

interface EditorWrapperProps {
  content: string
  activity: any
  course: any
  org: any
  onEditorReady?: () => void
}

function EditorWrapper(props: EditorWrapperProps): JSX.Element {
  const { t } = useTranslation()
  const { track } = useLHAnalytics('editor')
  const session = useLHSession() as any
  const access_token = session?.data?.tokens?.access_token;

  // Track the current version we loaded with
  const [localVersion, setLocalVersion] = React.useState<number>(
    props.activity.current_version || 1
  )
  const [_localUpdateDate, setLocalUpdateDate] = React.useState<string>(
    props.activity.update_date || ''
  )

  // Fix AI-generated mark types (strong -> bold, em -> italic) and drop empty
  // text nodes — ProseMirror rejects those and TipTap silently swaps the whole
  // document for an empty one, which here would mean opening the editor on a
  // blank page and saving that blank over real content. See sanitizeContent.ts.
  // Sanitising unconditionally is both safer and cheaper than pre-scanning: the
  // API hands us an object, so a regex pre-scan has to stringify the whole
  // document first — which costs more than the walk it was meant to skip.
  const normalizedContent = React.useMemo(() => {
    if (!props.content) return props.content;
    try {
      const parsed = typeof props.content === 'string'
        ? JSON.parse(props.content)
        : props.content;
      return sanitizeTiptapContent(parsed);
    } catch (_e) {
      // If parsing fails, return original content
      return props.content;
    }
  }, [props.content]);

  // Kick off non-awaited dynamic imports for the node-view chunks needed by
  // the blocks present in this document, so they're cached before TipTap renders.
  React.useEffect(() => {
    preloadComponentsForContent(normalizedContent)
  }, [normalizedContent]);

  // Check for remote changes (conflict detection)
  const checkForConflicts = React.useCallback(async (): Promise<ConflictInfo | null> => {
    if (!access_token) return null;

    try {
      const remoteState = await getActivityState(
        props.activity.activity_uuid,
        access_token
      );

      // Check if remote version is newer than our local version
      const hasConflict = remoteState.current_version > localVersion;

      return {
        hasConflict,
        remoteVersion: remoteState.current_version,
        localVersion,
        lastModifiedBy: remoteState.last_modified_by_username,
        lastModifiedAt: remoteState.update_date,
      };
    } catch (error) {
      console.error('Error checking for conflicts:', error);
      return null;
    }
  }, [props.activity.activity_uuid, access_token, localVersion]);

  // Fetch remote content for merging
  const fetchRemoteContent = React.useCallback(async () => {
    if (!access_token) return null;

    try {
      const remoteActivity = await getActivity(
        props.activity.activity_uuid,
        null,
        access_token
      );
      return remoteActivity.content;
    } catch (error) {
      console.error('Error fetching remote content:', error);
      return null;
    }
  }, [props.activity.activity_uuid, access_token]);

  async function setContent(content: any, forceOverwrite: boolean = false) {
    // Check for conflicts before saving (unless force overwrite)
    if (!forceOverwrite) {
      const conflictInfo = await checkForConflicts();
      if (conflictInfo?.hasConflict) {
        // Don't save if there's a conflict - the UI will handle this
        toast.error(
          t('editor.versioning.conflict.detected', {
            author: conflictInfo.lastModifiedBy || t('editor.versioning.conflict.another_teacher')
          })
        );
        return { hasConflict: true, conflictInfo };
      }
    }

    try {
      const result = await toast.promise(
        updateActivity({ content }, props.activity.activity_uuid, access_token).then(res => {
          if (!res.success) {
            throw res;
          }
          // Update local version after successful save
          if (res.data?.current_version) {
            setLocalVersion(res.data.current_version);
            setLocalUpdateDate(res.data.update_date);
          }
          track(AnalyticsEvent.ActivityContentSaved, {
            new_version: res.data?.current_version,
            had_conflict: false,
          });
          return res;
        }),
        {
          loading: t('editor.saving'),
          success: () => <b>{t('editor.saved')}</b>,
          error: (err) => {
            const errorMessage = err?.data?.detail || err?.data?.message || t('editor.save_error');
            return <b>{errorMessage}</b>;
          },
        }
      )
      return { hasConflict: false, result };
    } catch {
      // toast.promise already showed the error toast; return a non-throwing failure result
      return { hasConflict: false, result: null };
    }
  }

  return (
    <>
      <Toast></Toast>
      <OrgProvider orgslug={props.org.slug}>
        {!session.isLoading && (
          <Editor
            org={props.org}
            course={props.course}
            activity={props.activity}
            content={normalizedContent}
            setContent={setContent}
            session={session}
            checkForConflicts={checkForConflicts}
            fetchRemoteContent={fetchRemoteContent}
            localVersion={localVersion}
            onReady={props.onEditorReady}
          />
        )}
      </OrgProvider>
    </>
  )
}

export default EditorWrapper