# manhwa-mvp

MVP local de génération de vidéos YouTube de **narration de manhwa en français**.
Pipeline 100 % Python (+ FFmpeg + Piper TTS), sans n8n, sans scraping de scans.

---

## 1. Objectif

À partir :
- d'un **titre** de manhwa
- d'un **dossier local de scans** (images fournies par toi)

Produire automatiquement :
- une **narration audio** (`narration.wav`) en français
- une **vidéo finale** (`final_video.mp4`) prête à uploader

Les **scans ne sont pas la source narrative principale**. Ils servent uniquement à :
1. capter des **indices émotionnels** via OCR (onomatopées, exclamations…)
2. fournir un **support visuel transformé** (zoom lent, vignette, contraste).

La narration est construite à partir d'**AniList**, de **wikis/fandom** et de **discussions communautaires Reddit**, puis reformulée par **Gemini 2.5 Pro** de manière transformative.

---

## 2. Architecture

```
manhwa-mvp/
├── README.md
├── requirements.txt
├── .env.example
├── config.json
├── run_pipeline.py            # orchestrateur principal
├── scripts/
│   ├── scrape_anilist.py      # AniList GraphQL → anilist.json
│   ├── scrape_wiki.py         # Fandom/wiki HTML → wiki.json
│   ├── scrape_community.py    # Reddit (PRAW) → community.json
│   ├── ocr_scans.py           # PaddleOCR scans → ocr.json
│   ├── build_context.py       # fusion → context.json
│   ├── generate_script_gemini.py  # Gemini → script.txt
│   ├── qa_script.py           # QA locale (+ option LLM) → qa.json
│   ├── tts_piper.sh           # Piper → narration.wav
│   ├── transform_images.sh    # FFmpeg per-image → segments
│   └── render_video.sh        # FFmpeg concat + audio → final_video.mp4
├── prompts/
│   ├── narrative_system.txt
│   ├── narrative_user.txt
│   └── qa_prompt.txt
├── storage/
│   ├── input/scans/<dossier_du_titre>/   # tes scans .jpg/.png
│   ├── temp/                             # JSON intermédiaires + segments
│   ├── output/                           # narration.wav + final_video.mp4
│   └── models/                           # modèle Piper .onnx + .json
└── tests/
    └── test_build_context.py
```

---

## 3. Installation

### 3.1 Dépendances système

| Outil | macOS (brew) | Debian/Ubuntu |
|---|---|---|
| Python 3.11+ | `brew install python@3.11` | `sudo apt install python3.11 python3.11-venv` |
| FFmpeg | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| Piper TTS | voir 3.3 | voir 3.3 |
| wget | `brew install wget` | `sudo apt install wget` |

Vérifier :
```bash
ffmpeg -version
python3.11 --version
```

### 3.2 Installation Python

```bash
cd manhwa-mvp
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> ⚠️ **PaddlePaddle / PaddleOCR** : si l'installation pip échoue (notamment Apple Silicon),
> installe la roue spécifique depuis https://www.paddlepaddle.org.cn/en/install/quick

### 3.3 Installation Piper TTS

```bash
# macOS / Linux : binaire officiel
# https://github.com/rhasspy/piper/releases
# Récupère piper_macos_aarch64.tar.gz ou piper_linux_x86_64.tar.gz, extrait, mets `piper` dans le PATH.

piper --version
```

### 3.4 Téléchargement du modèle Piper FR (officiel)

```bash
mkdir -p storage/models
cd storage/models

# Modèle officiel Piper FR (rhasspy/piper-voices sur HuggingFace)
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx.json

cd ../..
```

### 3.5 Configuration `.env`

```bash
cp .env.example .env
# Édite .env et renseigne :
#   GEMINI_API_KEY   (https://aistudio.google.com/apikey)
#   REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
#   (https://www.reddit.com/prefs/apps → "create another app..." → script)
```

---

## 4. Utilisation

### 4.1 Ajouter les scans

Place tes images dans un sous-dossier de `storage/input/scans/` :
```
storage/input/scans/solo_leveling/
    001.jpg
    002.jpg
    ...
```
Formats supportés : `.jpg`, `.jpeg`, `.png`, `.webp`.

### 4.2 Configurer le titre

Édite `config.json` :
```json
{
  "title": "Solo Leveling",
  ...
}
```

Pour des scans coréens : `"ocr": { "lang": "korean" }`.
Pour FR : `"lang": "french"`. Pour EN : `"lang": "en"`.

### 4.3 Lancer le pipeline

```bash
source .venv/bin/activate
python run_pipeline.py
```

Options utiles :
```bash
python run_pipeline.py --skip-step community     # saute Reddit (si creds non config)
python run_pipeline.py --only-step gemini        # rejoue uniquement Gemini
python run_pipeline.py --config other_config.json
```

---

## 5. Sorties produites

| Fichier | Description |
|---|---|
| `storage/temp/anilist.json` | Données AniList brutes |
| `storage/temp/wiki.json` | Évènements extraits du wiki |
| `storage/temp/community.json` | Angles narratifs Reddit |
| `storage/temp/ocr.json` | OCR brut + indices émotionnels |
| `storage/temp/context.json` | Contexte narratif fusionné |
| `storage/temp/script.txt` | Script de narration (FR) |
| `storage/temp/qa.json` | Score de risque + recommandations |
| `storage/output/narration.wav` | Voix off Piper |
| `storage/output/final_video.mp4` | **Vidéo finale prête** |

---

## 6. Limites du MVP

- Pas d'upload YouTube, pas de thumbnail, pas de scheduling.
- Pas de timecodes synchronisés voix ↔ image (la vidéo est tronquée à la durée min audio/visuel).
- QA locale basique (option Gemini disponible via `"use_llm_qa": true`).
- Une seule langue OCR par run.
- Reddit peut renvoyer peu de contenu si le subreddit est petit.
- La narration n'est pas relue par un humain : **toujours vérifier** le script avant publication.

---

## 7. Pistes d'amélioration (préparées dans le design)

1. Remplacer Piper par **ElevenLabs** (interface `tts_*.sh` interchangeable).
2. Remplacer Gemini par **Claude / GPT** (`generate_script_*.py` modulaire).
3. Ajouter un **uploader YouTube** (étape 11 dans `run_pipeline.py`).
4. Ajouter des **timecodes audio** alignés sur les segments visuels.
5. **QA LLM** complète multi-passes.
6. Encapsuler le tout dans un **n8n** (chaque script reste autonome → wrappable).
7. **Multi-langues** narration (config + prompts FR/EN/ES…).
8. **Scraper de scans contrôlé** (étape 4 bis, opt-in, respect des CGU).
9. **Cache / SQLite** pour ne pas re-scraper systématiquement.
10. **Interface web** légère (FastAPI + HTMX) pour piloter le pipeline.

---

## 8. Règles narratives (rappel)

- AniList + wiki + communauté = **sources principales**.
- OCR = **renfort émotionnel uniquement**.
- **Jamais** de dialogue exact recopié.
- **Jamais** de découpage chapitre/page.
- Style **récit oral immersif**, pas un résumé scolaire.
- L'audio est le **contenu principal**, l'image est secondaire.


python run_pipeline.py --skip-step community            # si pas de creds Reddit
python run_pipeline.py --only-step gemini --only-step qa  # rejouer juste l'écriture
python run_pipeline.py --only-step transform --only-step render  # rebuild vidéo seule


brew upgrade ffmpeg
brew upgrade ffmpeg && ffmpeg -version
