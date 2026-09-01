"""HI-HA (C1, revue U6d) — schema de base applique au boot, AVANT le serveur.

Pourquoi ce script existe : le demarrage de l'API ne fait que
``SQLModel.metadata.create_all`` (src/core/events/database.py), qui cree les
tables manquantes mais n'emet JAMAIS d'ALTER TABLE. Toute colonne ajoutee par
une release sur une table existante (ex. ``organization.is_demo``, migration
``c7d8e9f0a1b2`` de la 1.3.5) n'existerait donc jamais sur une base deja
installee -> UndefinedColumn -> 500 sur tout le site.

Pourquoi pas un simple ``alembic upgrade heads`` inconditionnel : la chaine de
migrations amont ne sait PAS construire une base depuis zero (la migration
initiale ``df2981bf24dd`` fait des ALTER sur des tables supposees exister), et
une base installee par ``create_all`` seul n'a pas de table
``alembic_version`` — un ``upgrade heads`` y rejouerait TOUTE la chaine et
echouerait (la migration initiale retire des colonnes qui n'existent plus).
Trois etats sont donc distingues :

1. ``alembic_version`` non vide  -> regime de croisiere : ``upgrade heads``.
2. base installee sans ``alembic_version`` (le cas de la production campus,
   installee par ``create_all`` le 2026-08-08) -> adoption unique : ``stamp``
   des 4 tetes du code qui a construit ce schema (etat ``68e35317``, verifie
   par calcul du graphe des revisions a ce commit), puis ``upgrade heads``
   qui n'applique que les migrations reellement nouvelles (``b1n2u3d4g5e6``
   puis ``c7d8e9f0a1b2``, qui porte ``organization.is_demo``).
3. base vierge -> les migrations ne peuvent rien construire ; ``create_all``
   du demarrage applicatif va creer le schema courant (qui EST l'etat heads) :
   on ``stamp heads`` pour que les boots suivants prennent le regime 1.

Topologie reelle du graphe (verifiee par ``alembic heads``/``branches`` dans
l'image construite — pas par lecture des fichiers, dont les down_revision en
tuple multi-lignes trompent un grep) : une SEULE tete effective,
``c7d8e9f0a1b2`` ; les 3 anciennes branches paralleles fusionnent en
``e6f7a8b9c0d1_merge_heads``. La revue U6d annoncait « 4 tetes » sur la foi
d'une lecture regex qui manquait ce tuple. ``upgrade heads`` (PLURIEL) est
retenu quand meme : equivalent a ``head`` quand la tete est unique, et
robuste si l'amont recree des tetes multiples.

Echec = code retour non nul = echec de boot (docker/start.sh et
docker-entrypoint.sh s'arretent avant de lancer quoi que ce soit) : le
healthcheck reste rouge et l'orchestrateur (rolling update Coolify) conserve
l'ancien conteneur.

Ce script doit etre lance depuis apps/api (alembic.ini et migrations/ y
vivent, et migrations/env.py importe ``config.config`` relativement).
"""

import os
import sys
import time

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

# Tete du graphe alembic a l'etat 68e35317 — le dernier code deploye ayant
# construit/synchronise le schema de production par create_all, avant
# l'adoption des migrations. C'est la base de reference du « stamp » du cas 2 :
# les deux seules migrations posterieures (b1n2u3d4g5e6 puis c7d8e9f0a1b2)
# sont alors appliquees par l'upgrade. Tete unique (add_user_mfa_tables) :
# les anciennes branches fusionnent plus bas, en e6f7a8b9c0d1_merge_heads.
PRE_ADOPTION_HEAD = "t6u7v8w9x0y1"

_CONNECT_ATTEMPTS = int(os.environ.get("LEARNHOUSE_DB_STARTUP_ATTEMPTS", "5"))
_CONNECT_BACKOFF_SECONDS = 2.0


def _database_url() -> str:
    url = os.environ.get("LEARNHOUSE_SQL_CONNECTION_STRING", "")
    if not url:
        # Repli developpement : l'URL de alembic.ini (localhost).
        url = Config("alembic.ini").get_main_option("sqlalchemy.url") or ""
    # Coolify/Heroku/Render distribuent le prefixe court `postgres://`, que
    # SQLAlchemy 1.4+ ne connait plus (meme piege que cli.py, entree NOTICE du
    # 2026-08-08) ; alembic utilise psycopg2 (sync), pas asyncpg.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _inspect_state(url: str):
    """(alembic_version non vide, table organization presente) — avec retry.

    Le retry ne couvre que la base pas encore prete (demarre en parallele) ;
    une erreur permanente (mauvais mot de passe...) ressort apres la derniere
    tentative et fait echouer le boot.
    """
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    last_error: BaseException = RuntimeError("database never reachable")
    try:
        for attempt in range(1, max(1, _CONNECT_ATTEMPTS) + 1):
            try:
                with engine.connect() as conn:
                    inspector = sa.inspect(conn)
                    tables = set(inspector.get_table_names())
                    version_rows = []
                    if "alembic_version" in tables:
                        version_rows = [
                            row[0]
                            for row in conn.execute(
                                sa.text("SELECT version_num FROM alembic_version")
                            )
                        ]
                    return bool(version_rows), "organization" in tables
            except Exception as error:
                last_error = error
                if attempt < _CONNECT_ATTEMPTS:
                    delay = _CONNECT_BACKOFF_SECONDS * attempt
                    print(
                        f"boot-migrations: base injoignable (tentative "
                        f"{attempt}/{_CONNECT_ATTEMPTS}), nouvel essai dans "
                        f"{delay:.0f}s : {error}",
                        flush=True,
                    )
                    time.sleep(delay)
    finally:
        engine.dispose()
    raise last_error


def main() -> int:
    url = _database_url()
    if not url:
        print(
            "boot-migrations: aucune URL de base de donnees "
            "(LEARNHOUSE_SQL_CONNECTION_STRING absente, alembic.ini vide) — abandon.",
            file=sys.stderr,
        )
        return 1
    # migrations/env.py relit cette variable : lui transmettre l'URL normalisee.
    os.environ["LEARNHOUSE_SQL_CONNECTION_STRING"] = url

    has_versions, has_core = _inspect_state(url)
    config = Config("alembic.ini")

    if has_versions:
        print(
            "boot-migrations: alembic_version presente -> alembic upgrade heads",
            flush=True,
        )
        command.upgrade(config, "heads")
    elif has_core:
        print(
            "boot-migrations: base installee sans alembic_version (schema "
            f"create_all) -> adoption : stamp {PRE_ADOPTION_HEAD} "
            "puis alembic upgrade heads",
            flush=True,
        )
        command.stamp(config, PRE_ADOPTION_HEAD)
        command.upgrade(config, "heads")
    else:
        print(
            "boot-migrations: base vierge -> le create_all du demarrage "
            "applicatif construira le schema courant (= etat heads) ; "
            "stamp heads.",
            flush=True,
        )
        command.stamp(config, "heads")

    print("boot-migrations: schema au niveau heads.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
