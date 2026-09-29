# Changelog

## 1.3.4
- **Carte : thèmes clairs pris en charge.** Le texte ne s'affiche plus en noir sur le fond sombre de la carte avec un thème clair (Google, thème par défaut…).
- Nouvelle option **Apparence** (`theme`) : Automatique (suit le mode clair / sombre, par défaut), Sombre, Clair, ou Couleurs de mon thème Home Assistant.

## 1.3.3
- **Assistant de configuration plus robuste** : la liste des stations ne dépend plus de la vitesse d'OpenStreetMap. Si OSM est lent ou indisponible, l'assistant continue aussitôt (noms récupérés en arrière-plan) au lieu de tourner puis d'échouer sur « Erreur inconnue » (notamment derrière un proxy qui coupe au bout de 60 s).
- Délais réseau réduits dans l'assistant ; toute erreur inattendue affiche un message clair au lieu d'une erreur inconnue.
- La mise à jour des prix n'attend plus OpenStreetMap plus de 20 s.

## 1.3.2
- Validation HACS complète (plus aucune vérification désactivée), préparation à l'inscription dans la liste officielle HACS.

## 1.3.1
- Icône de l'intégration fournie avec l'intégration (dossier `brand`), affichée par Home Assistant 2026.3 et plus.

## 1.3.0
- **Intégration autonome** : noms, enseignes et logos des stations issus d'OpenStreetMap et de Wikidata / Wikimedia Commons (mis en cache), plus aucune dépendance à une autre intégration.
- Attribution des sources sur les capteurs.

## 1.2.0
- Noms et enseignes des stations téléchargés depuis la liste communautaire et mis en cache chaque semaine (plus besoin du fichier local).
- Publication sur GitHub, compatible HACS (mises à jour depuis Home Assistant).
- Documentation complète.

## 1.1.1
- Carte : bouton d'actualisation dans la vue compacte.

## 1.1.0
- Carte : nouvelle **vue compacte** (tableau des stations, J+n, pastilles moins cher / plus cher, option « uniquement mes favorites »).
- Actualisation réglable jusqu'à 24 h.

## 1.0.0
- Première version : zones autour d'une ville (carte + rayon), favorites, meilleur prix / moyenne / top 5 par carburant, historique 120 jours et tendances, carte `holm-fuel-card` enregistrée automatiquement.
