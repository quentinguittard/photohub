# PhotoHub ✨

PhotoHub est un outil de flux de travail (workflow) haute performance conçu pour les photographes professionnels. Il centralise l'ingestion, le tri, l'édition rapide et l'exportation multi-format dans une interface moderne et intuitive.

## 🚀 Fonctionnalités Clés

### 📁 Gestion de Projet & Ingestion
- **Hub Centralisé** : Gérez tous vos shootings depuis une vue unique.
- **Importation Sécurisée** : Ingestion avec vérification d'intégrité (checksum).
- **Structure Flexible** : Support du stockage global configurable et des dossiers par projet.

### 🎯 Tri & Sélection (Culling)
- **Tri Ultra-Rapide** : Navigation fluide avec préchargement des images.
- **Notation & Rejet** : Systèmes de notes (1-5★) et drapeaux (P/X) avec raccourcis clavier.
- **Filtres Avancés** : Filtrez par note, statut ou type de fichier instantanément.

### 🎨 Édition & Post-Traitement Rapide
- **Ajustements Non-Destructifs** : Exposition, balance des blancs, contraste, saturation, etc.
- **Correction Géométrique** : Recadrage et redressement de l'horizon.
- **Synchronisation en Lot** : Copiez-collez ou synchronisez vos réglages sur des centaines d'images.
- **Aperçu Avant/Après** : Comparez vos modifications en temps réel.

### 📦 Exportation & Livraison
- **Profils Multiples** : Exportez simultanément pour le `web`, l' `impression` et les `réseaux sociaux`.
- **Marquage (Watermark)** : Système de filigrane dynamique par presets.
- **Packaging de Livraison** : Génération automatisée de fichiers ZIP et rapports d'exportation.
- **Planches de Contact** : Génération de PDF professionnels pour vos clients.

## 💎 Identité Visuelle & Personnalisation

PhotoHub s'adapte à votre studio :
- **Dashboard Premium** : Tableau de bord de type "Bento Box" avec KPIs en temps réel.
- **Bannière Personnalisée** : Ajoutez votre propre bannière (avec recadrage intelligent) pour un accueil qui vous ressemble.
- **Couleur d'Accentuation** : Personnalisez l'interface aux couleurs de votre marque.
- **Profil Studio** : Signature automatique des métadonnées et avis de copyright.

## 🛠️ Automatisation & Performance
- **Système de Presets** : Presets de renommage, d'importation, d'exportation et de watermark versionnés.
- **Patterns de Nommage** : Moteur puissant utilisant des tags (`{project}`, `{date}`, `{seq:04d}`, `{orig}`) pour des fichiers parfaitement organisés.
- **Files d'Attente (Jobs)** : Gestionnaire de tâches en arrière-plan avec barre de progression globale et centre de suivi.
- **Surveillance Disque** : Monitoring en temps réel de l'espace de stockage avec alertes visuelles.

## 💻 Installation Rapide

```bash
# Initialiser l'environnement
python -m venv .venv
.venv\Scripts\activate

# Installer en mode développement
pip install -e .

# Lancer l'application
photohub
```

*Optionnel : Pour l'interface Fluent (Windows) :*
```bash
pip install -e ".[fluent]"
```

## ⌨️ Raccourcis Principaux

| Module | Action | Raccourci |
|--------|--------|-----------|
| **Tri** | Notation | `0` .. `5` |
| **Tri** | Garder / Rejeter | `P` / `X` |
| **Tri** | Navigation | `<-` / `->` |
| **Édition** | Copier / Coller réglages | `Ctrl+C` / `Ctrl+V` |
| **Édition** | Synchroniser sélection | `Shift+S` |
| **Édition** | Avant / Après | `Y` |

---
*PhotoHub — Développé pour la performance et le confort des photographes.*
