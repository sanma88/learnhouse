# README — Fork HI-HA de LearnHouse

Ce dépôt est le fork HI-HA de [LearnHouse](https://github.com/learnhouse/learnhouse) (AGPL-3.0) —
voir [NOTICE-HIHA.md](./NOTICE-HIHA.md) pour la provenance, la licence et la liste des modifications.
La branche **`hiha`** est la branche d'exploitation : elle est basée sur la dernière release stable
upstream (actuellement le tag `1.3.4`) et ne porte que nos ajouts propres.

## Remotes

- `origin` : le fork GitLab (`git@git.sanfilippo.be:mino/learnhouse.git`) — lecture/écriture,
  source du déploiement (GitLab intranet).
- `github` : le **miroir public** `git@github.com:sanma88/learnhouse.git` — publication AGPL
  art. 13 : la branche `hiha` doit y être poussée après chaque changement déployé.
- `upstream` : le dépôt GitHub d'origine — **lecture seule**. Le push y est désactivé
  (`git remote set-url --push upstream DISABLED`) : **ne jamais pousser vers upstream**.

## Synchronisation avec l'upstream

### 1. Récupérer les nouveautés upstream

```bash
git fetch upstream --tags
```

### 2. Mettre à jour les branches miroir (`dev`, `main`)

Fast-forward uniquement (ces branches restent des copies exactes de l'upstream) :

```bash
git checkout dev
git merge --ff-only upstream/dev
git push origin dev

git checkout main
git merge --ff-only upstream/main
git push origin main
```

### 3. Montée de version de `hiha` (voie normale : merge du nouveau tag)

Quand une nouvelle release stable `X.Y.Z` est publiée :

```bash
git checkout hiha
git merge X.Y.Z
git push origin hiha
git push github hiha   # miroir public (AGPL art. 13)
```

En cas de conflit : il ne peut normalement toucher que nos fichiers `-HIHA`
(et, à l'avenir, d'éventuels patchs de rebranding) — résoudre en conservant nos ajouts
tout en prenant les changements upstream partout ailleurs.

Alternative possible (historique linéaire) : rebaser nos commits sur le nouveau tag
(`git rebase --onto X.Y.Z <ancien-tag> hiha`), mais le **merge est la voie documentée**.

### 4. Tenir la NOTICE à jour

À chaque montée de version, ajouter une ligne au tableau « Modifications » de
[NOTICE-HIHA.md](./NOTICE-HIHA.md) (date + version mergée), puis committer et pousser.
