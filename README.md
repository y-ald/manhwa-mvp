# manhwa-mvp (v0.2)

MVP local de génération de vidéos YouTube de **narration de manhwa en français**, avec **support PDF**, **script structuré en scènes**, **TTS par scène** et **alignement image–narration**.

Pipeline 100 % Python (+ FFmpeg + Piper TTS), sans n8n, sans scraping de scans.

---

## 1. Objectif

À partir :
- d'un **titre** de manhwa
- d'un **dossier local de scans** (`.jpg`, `.png`, `.webp`) **et/ou de PDF**

Produire automatiquement :
- une **narration audio** (`narration.wav`) découpée par scènes en français
- une **vidéo finale** (`final_video.mp4`) prête à uploader, avec image alignée sur le texte

Les scans sont **un support secondaire** : la narration vient principalement d'AniList, des wikis/fandoms et des discussions Reddit, reformulée par Gemini de manière transformative.

---

## 2. Nouveautés v0.2 vs v0.1

| Domaine | v0.1 | v0.2 |
|---|---|---|
| Entrées | images uniquement | images **+ PDF** (extraction PyMuPDF) |
| Webtoon long | 1 image = 1 segment | PDF fusionné verticalement puis re-tranché en chunks 720 px |
| Script Gemini | texte plat | **JSON structuré en scènes** avec `tone`, `image_keywords`, `duration_hint_sec` |
| TTS | 1 wav géant | **1 wav par scène** + concat avec silences de respiration |
| Sync image/audio | aucune | **Distribution proportionnelle** des images aux scènes selon durée audio réelle |
| Effets vidéo | preset unique | **Preset par tone** (mystérieux, sombre, action, émotion, neutre) |
| Tri des fichiers | lexicographique | **Tri naturel** (`natsort`) |
| QA | sur texte plat | sur scènes structurées |

---

## 3. Architecture

```
manhwa-mvp/
├── README.md
├── requirements.txt
├── .env.example
├── config.json
├── run_pipeline.py            # orchestrateur 12 étapes
├── scripts/
│   ├── prep_inputs.py         # PDF + images -> slices PNG normalisées
│   ├── scrape_anilist.py      # AniList GraphQL
│   ├── scrape_wiki.py         # Fandom + Wikipedia fallback
│   ├── scrape_community.py    # Reddit (PRAW)
│   ├── ocr_scans.py           # PaddleOCR sur slices
│   ├── build_context.py       # fusion sources -> context.json
│   ├── generate_scenes_gemini.py  # Gemini -> scenes.json (JSON structuré)
│   ├── qa_script.py           # QA sur scenes.json
│   ├── tts_per_scene.py       # Piper par scène + scene_timeline.json
│   ├── plan_video.py          # match scenes <-> slices -> scene_plan.json
│   ├── transform_images.py    # FFmpeg avec presets par tone
│   └── render_video.sh        # concat + mux audio -> final_video.mp4
├── prompts/
│   ├── narrative_scenes_system.txt
│   ├── narrative_scenes_user.txt
│   └── qa_prompt.txt
├── storage/
│   ├── input/scans/<titre>/   # tes .jpg / .png / .pdf
│   ├── temp/
│   │   ├── scans_prepared/    # généré par prep_inputs
│   │   ├── scene_audio/       # 1 wav par scène
│   │   └── video_segments/    # 1 mp4 par slice
│   ├── output/                # narration.wav + final_video.mp4
│   └── models/                # modèle Piper .onnx
└── tests/
    ├── test_build_context.py
    ├── test_prep_inputs.py
    └── test_plan_video.py
```

---

## 4. Flux pipeline (12 étapes)

```
1.  prep_inputs   PDFs + images -> slices PNG normalisées 1280px
2.  anilist       métadonnées canoniques (titre, genres, description)
3.  wiki          événements narratifs depuis Fandom/Wikipedia
4.  community     angles Reddit (PRAW)
5.  ocr           PaddleOCR sur slices (cues émotionnels)
6.  context       fusion sources -> context.json
7.  scenes        Gemini -> scenes.json (5-10 scènes balisées)
8.  qa            heuristiques + option LLM -> qa.json
9.  tts           Piper par scène -> narration.wav + scene_timeline.json
10. plan          distribution slices <-> scenes -> scene_plan.json
11. transform     FFmpeg par scène (zoom/grain/vignette par tone)
12. render        concat segments + mux narration -> final_video.mp4
```

---

## 5. Installation

### 5.1 Dépendances système

| Outil | macOS | Debian/Ubuntu |
|---|---|---|
| Python 3.11+ | `brew install python@3.11` | `sudo apt install python3.11 python3.11-venv` |
| FFmpeg (récent) | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| Piper TTS | binaire GitHub releases | binaire GitHub releases |
| wget | `brew install wget` | `sudo apt install wget` |

### 5.2 Installation Python

```bash
cd manhwa-mvp
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 5.3 Modèle Piper FR officiel

```bash
mkdir -p storage/models && cd storage/models
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json
cd ../..
```

### 5.4 Configuration `.env`

```bash
cp .env.example .env
# Renseigne :
#   GEMINI_API_KEY               https://aistudio.google.com/apikey
#   REDDIT_CLIENT_ID             https://www.reddit.com/prefs/apps (script)
#   REDDIT_CLIENT_SECRET
#   REDDIT_USER_AGENT
```

---

## 6. Utilisation

### 6.1 Ajouter les scans / PDF

**Convention de nommage (importante)** : chaque manhwa a son propre sous-dossier nommé d'après le **slug** du titre (minuscules, accents retirés, espaces et ponctuation → `_`). Ainsi un seul `storage/input/scans/` peut héberger plusieurs œuvres sans mélange.

```
storage/input/scans/solo_leveling/                          # title = "Solo Leveling"
    chapitre_01.pdf
    chapitre_02.pdf
    bonus_001.jpg
storage/input/scans/the_3rd_prince_of_the_fallen_kingdom_returns/   # title = "The 3rd Prince of the Fallen Kingdom Returns"
    chapitre1_page1.pdf
    ...
```

Mappings courants (faits par `slugify()` dans `run_pipeline.py`) :

| Titre                                            | Sous-dossier attendu                              |
| ------------------------------------------------ | ------------------------------------------------- |
| `Solo Leveling`                                  | `solo_leveling`                                   |
| `Tower of God: Récits`                           | `tower_of_god_recits`                             |
| `The 3rd Prince of the Fallen Kingdom Returns`   | `the_3rd_prince_of_the_fallen_kingdom_returns`    |

Si le slug ne matche aucun sous-dossier, le pipeline échoue avec un message qui **liste les sous-dossiers disponibles**.

Tri **naturel** géré à l'intérieur du dossier : `chapitre_2.pdf` passe avant `chapitre_10.pdf`.

### 6.2 Configurer le titre

`config.json` :
```json
{
  "title": "Solo Leveling",
  "ocr": { "lang": "en" },         // ou "french" / "korean"
  "scenes": { "min_scenes": 5, "max_scenes": 10 }
}
```

Override pour un seul run (sans toucher au config) :

```bash
python run_pipeline.py --title "The 3rd Prince of the Fallen Kingdom Returns"
```

### 6.3 Lancer

```bash
source .venv/bin/activate
python run_pipeline.py
```

Options utiles :
```bash
python run_pipeline.py --skip-step community       # sans creds Reddit
python run_pipeline.py --only-step scenes          # rejouer Gemini
python run_pipeline.py --only-step plan --only-step transform --only-step render
```

---

## 7. Sorties

| Fichier | Description |
|---|---|
| `storage/temp/scans_prepared/` | Slices PNG normalisées (1280×720) |
| `storage/temp/anilist.json` | Données AniList |
| `storage/temp/wiki.json` | Évènements wiki |
| `storage/temp/community.json` | Snippets Reddit |
| `storage/temp/ocr.json` | OCR + cues émotionnels |
| `storage/temp/context.json` | Contexte fusionné |
| `storage/temp/scenes.json` | **Script structuré en scènes** |
| `storage/temp/qa.json` | Score de risque QA |
| `storage/temp/scene_audio/` | 1 wav par scène |
| `storage/temp/scene_timeline.json` | Timecodes audio précis |
| `storage/temp/scene_plan.json` | Plan slices↔scènes |
| `storage/temp/video_segments/` | 1 mp4 par slice |
| `storage/output/narration.wav` | Voix off concaténée |
| `storage/output/final_video.mp4` | **Vidéo finale** |

---

## 8. Format `scenes.json`

```json
{
  "title": "Solo Leveling",
  "language": "fr",
  "scenes": [
    {
      "id": 1,
      "type": "hook",
      "text": "Dans un monde où des portails déversent leurs monstres...",
      "tone": "mystérieux",
      "image_keywords": ["portail", "monstre", "ville"],
      "duration_hint_sec": 8
    }
  ]
}
```

**Tones supportés** (mappés à un preset FFmpeg dans `transform_images.py`) :
- `mystérieux` — zoom lent, grain léger, vignette forte, saturation basse
- `sombre` — zoom très lent, grain moyen, vignette très forte
- `action` — zoom rapide, grain fort, contraste élevé
- `émotion` — zoom très lent, sans grain, doux
- `neutre` — preset par défaut

---

## 9. Tests

```bash
pytest -q
```

Tests inclus :
- `test_build_context.py` — fusion des sources
- `test_prep_inputs.py` — PDF, images, slicing
- `test_plan_video.py` — distribution scènes ↔ slices

---

## 10. Limites du MVP v0.2

- Matching scène ↔ image **séquentiel proportionnel** (pas encore sémantique). Voir v0.5.
- Pas de musique de fond ni SFX (voir v0.3).
- Voix Piper TTS (mécanique) — voir v0.3 pour adaptateur ElevenLabs.
- Pas de cross-fade entre segments (voir v0.4).
- Pas d'upload YouTube, pas de thumbnail, pas de scheduling.
- QA locale heuristique (option `--use-llm` disponible).
- **Toujours vérifier le script avant publication** : la narration n'est pas relue par un humain.

---

## 11. Pistes d'amélioration (roadmap)

| Version | Contenu |
|---|---|
| v0.3 | Adaptateur ElevenLabs, mix musique de fond, watermark, cap durée image (anti Content ID) |
| v0.4 | Cross-fade FFmpeg `xfade`, drawtext titres, LUT cinemagraph, SFX library |
| v0.5 | Matching Gemini Vision multimodal (image keyword → image réelle) |
| v1.0 | Uploader YouTube, thumbnails IA, dashboard web, multi-langue |

---

## 12. Sécurité (rappels)

- **FFmpeg** est l'élément le plus exposé. Garde-le à jour : `brew upgrade ffmpeg`.
- N'utilise que des **scans / PDF de sources fiables**. Un PNG/PDF malicieux peut exploiter une CVE FFmpeg ou PyMuPDF.
- Ne commit **jamais** ton `.env` (déjà dans `.gitignore`).
- Pour des sources douteuses, isole le pipeline dans Docker (`--network=none`).

---

## 13. Règles narratives (rappel)

- AniList + wiki + communauté = **sources principales**.
- OCR = **renfort émotionnel** uniquement.
- **Jamais** de dialogue exact recopié.
- **Jamais** de découpage chapitre/page.
- Style **récit oral immersif**, pas un résumé scolaire.
- L'audio est le **contenu principal**, l'image est secondaire.
