# NOTICE — Fork HI-HA de LearnHouse

Ce dépôt est un **fork de [LearnHouse](https://github.com/learnhouse/learnhouse)**,
logiciel libre sous licence **GNU AGPL-3.0** (voir [LICENSE](./LICENSE), conservé intégralement).
Copyright © LearnHouse et ses contributeurs.

- **Dépôt d'origine** : https://github.com/learnhouse/learnhouse
- **Révision d'origine** : release `1.3.4` (tag `1.3.4`, commit `fb5196517706d0179c1b9747c47d42e307990560`, publiée le 2026-07-27)
- **Date du fork** : 2026-08-04
- **Exploité par** : HI-HA — https://campus.hi-ha.be

## Modifications apportées par rapport à l'origine

> Cette liste est tenue à jour à chaque changement, conformément à l'AGPL-3.0 (art. 5a).

| Date | Modification |
|------|--------------|
| 2026-08-04 | Ajout de `NOTICE-HIHA.md` (ce fichier) et `README-HIHA.md` (procédure de synchronisation upstream). Aucune modification du code ni des mentions légales. |
| 2026-08-06 | Ajout de l'infrastructure de déploiement, sans aucune modification du code applicatif : `.gitlab-ci.yml` (build de l'image et déploiement), `docker-compose.hiha.local.yml` et `.env.hiha.local.example` (environnement de test local). |
| 2026-08-07 | **Première modification de code** — `docker/nginx.conf` : le nginx interne de l'image écrasait l'en-tête `X-Forwarded-Proto` par sa propre variable `$scheme` (8 occurrences). Derrière un proxy de bordure qui termine le TLS (Traefik), l'API recevait donc toujours `http` et émettait les cookies de session **sans l'attribut `Secure`** (`apps/api/src/routers/auth.py`, `is_request_secure`), tandis que les URL canoniques sortaient en `http://`. Correctif : une directive `map` propage l'en-tête entrant lorsqu'il vaut `https` ou `http`, et retombe sur `$scheme` en son absence (exécution locale ou accès direct). Aucun changement de comportement hors reverse-proxy. |

| 2026-08-07 | **Changement de marque (rebranding HI-HA).** Remplacement du contenu des fichiers d'images de marque, à noms de fichiers inchangés : `apps/web/public/lrn.svg`, `lrn-dash.svg`, `lrn-text.svg`, `learnhouse_bigicon_1.png`, `black_logo.png`, `favicon.ico`. Remplacement des libellés visibles (`alt`, titres de page, valeurs de repli) dans 22 composants, et des chaînes de l'écran de connexion (`auth.image_title_login`, `auth.image_title_signup`, `auth.terms_text`) ainsi que de `common.copyright` dans les 22 fichiers de langue. Le logo de l'écran de connexion, du pied de page d'organisation et du filigrane pointe désormais vers l'instance elle-même (`/`) au lieu de `learnhouse.app`. **Aucune mention légale n'a été retirée** : le fichier `LICENSE` (AGPL-3.0) est intact, le code amont ne porte aucun en-tête de copyright par fichier, et son interface n'affichait aucune « Appropriate Legal Notice » au sens de l'art. 5(d). |
| 2026-08-07 | `apps/web/components/Footers/LegalFooters.tsx` — deux corrections. (1) Les liens « Terms of Service » / « Privacy Policy » pointaient en dur vers `learnhouse.io` : sur une instance auto-hébergée, cela affirme à tort que les utilisateurs contractent avec LearnHouse, Inc. Ils ne s'affichent plus que si ce déploiement publie effectivement de telles pages (URL de plateforme configurée). (2) **Ajout du lien « Code source » (AGPL art. 13)** vers le miroir public, affiché sur l'écran de connexion et en pied de page, afin que les utilisateurs du service par le réseau se voient réellement offrir la source. URL surchargeable par `NEXT_PUBLIC_LEARNHOUSE_SOURCE_URL`. |
| 2026-08-07 | `apps/web/components/Emails/LearnHouseEmail.tsx` — les emails transactionnels (invitations, réinitialisation de mot de passe) affichaient le logo LearnHouse **hotlinké depuis `learnhouse.io`** — marque d'un tiers sur nos envois, et une requête vers ce tiers à chaque ouverture. Le logo est désormais servi par l'instance et la signature est configurable (`NEXT_PUBLIC_LEARNHOUSE_EMAIL_LOGO_URL`, `NEXT_PUBLIC_LEARNHOUSE_EMAIL_SIGNOFF`). |

## Code source (AGPL art. 13)

Toute personne utilisant ce logiciel via le réseau peut obtenir le code source
de la version exécutée, y compris ces modifications, depuis le miroir public :
**https://github.com/sanma88/learnhouse** (branche `hiha`).
