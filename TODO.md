# TODO — Fork HI-HA de LearnHouse

Travaux prévus sur ce fork et sur son exploitation. Voir [README-HIHA.md](./README-HIHA.md)
pour la branche d'exploitation et la synchronisation upstream, et
[NOTICE-HIHA.md](./NOTICE-HIHA.md) pour la liste des modifications déjà apportées.

> Ce dépôt a un **miroir public** (AGPL art. 13). N'y consigner aucun identifiant,
> aucune procédure d'authentification, aucune valeur de configuration d'exploitation.

---

## Suivi de progression des apprenants — restitution par formation

**Besoin.** Permettre à un formateur d'obtenir, pour une formation donnée, l'avancement
de ses apprenants : qui est inscrit, qui a terminé, où en est chacun activité par activité.

**Approche retenue.** Un workflow [n8n](https://n8n.io) : un formulaire où le formateur
choisit la formation, puis des appels à l'**API Admin** de LearnHouse, et enfin un tableau
apprenants × avancement exportable.

**Pourquoi cette voie.** Les surfaces intégrées ne conviennent pas à un déploiement
auto-hébergé en édition libre :

| Voie | Verdict |
|---|---|
| Tableau de bord `/dash/analytics` | Nécessite Tinybird, un service tiers externe |
| Fiche apprenant `/dash/users/analytics/[userId]` | `analytics_advanced` ∈ `EE_ONLY_FEATURES` → HTTP 403 en mode OSS |
| Lever le verrou EE dans le fork | Possible, mais dette de maintenance à chaque synchronisation upstream |
| **API Admin, consommée par n8n** | **Retenu** — aucune modification du code, aucune dépendance tierce |

**Points d'entrée utiles** (routeur `/admin/{org_slug}/…`, jeton d'organisation) :

| Endpoint | Retour |
|---|---|
| `GET /admin/{org_slug}/courses/{course_uuid}/analytics` | inscrits, terminés, en cours, total d'activités, pourcentage moyen, certificats |
| `GET /admin/{org_slug}/courses/{course_uuid}/enrollments` | liste des inscrits, paginée |
| `GET /admin/{org_slug}/trails/{user_id}/courses/{course_uuid}` | par chapitre puis par activité : `completed`, `completed_at`, `grade`, `teacher_verified` |
| `GET /courses/org_slug/{org_slug}/page/{page}/limit/{limit}` | liste des cours, pour peupler le formulaire |

### Ce que ces données disent — et ne disent pas

À lire avant de nommer les colonnes du rapport : mal libellées, elles seront
sur-interprétées.

- **La progression est déclarative.** Une étape de parcours n'est créée que lorsque
  l'apprenant clique lui-même sur « Terminé » (`POST /trail/add_activity/{activity_uuid}`),
  et il peut annuler ce marquage. Ce n'est pas une mesure d'activité réelle.
- **Rien n'est enregistré à l'ouverture d'une activité.** « Jamais ouverte » et
  « ouverte mais non terminée » sont indiscernables : dans les deux cas, aucune étape.
- **Aucun temps passé n'est conservé.** Le frontend mesure la durée par activité et
  l'émet vers le collecteur d'analytique ; sans celui-ci configuré, la mesure est perdue
  et n'est jamais écrite en base.
- **`grade` et `teacher_verified` sont des champs morts** sur les étapes de parcours :
  écrits vides à la création, jamais renseignés ensuite. Les notes réelles vivent dans
  les tables de devoirs.

### Précautions

- **Données personnelles.** Le point d'entrée des inscrits renvoie nom, adresse
  électronique et biographie. Un formulaire n8n n'est pas protégé par défaut : l'exposer
  sans authentification équivaudrait à publier ces données.
- **Fiabilité des compteurs.** Les contraintes d'unicité `uq_trailrun_trail_course_user`
  et `uq_trailstep_run_activity_user` sont déclarées dans les modèles avec la mention
  « requires a matching migration to apply to the live DB ». Vérifier que la migration
  est bien passée avant de publier des pourcentages : sans elle, un double marquage
  reste possible et gonflerait les taux de complétion.

### Terminé quand

Un formateur obtient le tableau apprenants × avancement d'une formation depuis le
formulaire, sans ligne de commande, avec export ; le formulaire est protégé ; aucun
secret n'apparaît dans le workflow exporté.
