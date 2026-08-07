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

## Code source (AGPL art. 13)

Toute personne utilisant ce logiciel via le réseau peut obtenir le code source
de la version exécutée, y compris ces modifications, depuis le miroir public :
**https://github.com/sanma88/learnhouse** (branche `hiha`).
