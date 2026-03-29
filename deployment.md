# Deployment Guide — Suitability Engine

## Overview

```
GitHub repo  →  Render (API)  ←  S3 (raster data)
                    ↑
              React frontend
              (Render static site or local)
```

The API is **stateless between requests** — all rasters are loaded into memory
at startup from S3. No database required.

---

## Step 1 — Verify your file structure

Your repo must look like this before pushing:

```
suitability-engine/
├── config/
│   ├── active_county.txt      ← "kitui"
│   ├── kitui.json
│   └── bungoma.json
├── src/
│   ├── api.py                 ← updated (S3 sync)
│   ├── config.py              ← updated (__file__-relative CONFIG_DIR)
│   └── ... (other scripts)
├── frontend/
│   └── ...
├── render.yaml                ← updated (src.api:app)
├── requirements.txt           ← updated (pydantic, uvicorn[standard])
├── deploy_check.py            ← run this before every deploy
└── .gitignore                 ← data/ and *.tif excluded
```

Run the pre-deploy check:
```bash
python deploy_check.py
```
All checks must be ✅ before continuing.

---

## Step 2 — Verify your S3 structure

Your bucket (`suitability-engine`) must have this layout for each county:

```
suitability-engine/
└── kitui/
    ├── normalized/
    │   ├── normalized_elevation.tif
    │   ├── normalized_rainfall.tif
    │   ├── normalized_temperature.tif
    │   ├── normalized_soil.tif
    │   └── normalized_slope.tif
    ├── boundary/
    │   └── kitui_boundary.gpkg
    ├── constraints/
    │   └── protected_areas_kenya.gpkg
    ├── preprocessed/
    │   └── kitui_constraints_mask.tif
    └── results/               ← written here by the API after each analysis
```

Verify from the AWS console or CLI:
```bash
aws s3 ls s3://suitability-engine/kitui/ --recursive
```

---

## Step 3 — Push to GitHub

```bash
git add src/api.py src/config.py render.yaml requirements.txt deploy_check.py
git commit -m "feat: S3 sync, deployment fixes"
git push origin main
```

Make sure these are NOT committed (check .gitignore):
- `data/` directory (rasters)
- `*.tif`, `*.gpkg`, `*.geojson` files
- `venv/`

---

## Step 4 — Deploy on Render

### 4a. Create the Web Service

1. Go to [render.com](https://render.com) → **New** → **Web Service**
2. Connect your GitHub repo
3. Render will auto-detect `render.yaml` — confirm the settings:

| Setting | Value |
|---|---|
| Runtime | Python |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn src.api:app --host 0.0.0.0 --port $PORT` |
| Health check path | `/health` |

### 4b. Set secret environment variables

In the Render dashboard → **Environment** tab, add these manually
(they are marked `sync: false` in render.yaml so they're never stored in git):

| Key | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | Your AWS key |
| `AWS_SECRET_ACCESS_KEY` | Your AWS secret |

These are already set in render.yaml (non-secret):

| Key | Value |
|---|---|
| `SUITABILITY_DATA_DIR` | `/tmp/suitability-engine` |
| `ACTIVE_COUNTY` | `kitui` |
| `AWS_S3_BUCKET` | `suitability-engine` |
| `AWS_DEFAULT_REGION` | `eu-north-1` |

### 4c. Set your AWS IAM permissions

The IAM user/role needs these S3 permissions on your bucket:

```json
{
  "Effect": "Allow",
  "Action": [
    "s3:GetObject",
    "s3:PutObject",
    "s3:ListBucket"
  ],
  "Resource": [
    "arn:aws:s3:::suitability-engine",
    "arn:aws:s3:::suitability-engine/*"
  ]
}
```

`ListBucket` — needed for the paginator in sync
`GetObject`  — download normalized layers
`PutObject`  — upload results back to S3

---

## Step 5 — Verify the deploy

Once Render shows **Live**, check these URLs
(replace `your-service.onrender.com` with your actual URL):

```bash
# 1. Health check — should show all 5 layers loaded
curl https://your-service.onrender.com/health

# Expected:
# {
#   "status": "healthy",
#   "layers_loaded": 5,
#   "layers_expected": 5,
#   "boundary_available": true,
#   "constraint_mask": true,
#   ...
# }

# 2. County info
curl https://your-service.onrender.com/county

# 3. Run a test analysis
curl -X POST https://your-service.onrender.com/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "weights": {
      "rainfall": 0.30,
      "elevation": 0.15,
      "temperature": 0.20,
      "soil": 0.20,
      "slope": 0.15
    },
    "apply_constraints": true
  }'
```

If `layers_loaded` is 0, check the Render logs — S3 credentials or bucket
name are the most common cause.

---

## Step 6 — Connect the frontend

Update the API base URL in your React app:

**`frontend/src/components/MapView.js`** and **`frontend/src/App.js`**:
```js
// Change this:
const API_BASE_URL = 'http://localhost:8000';

// To this:
const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';
```

Then set `REACT_APP_API_URL=https://your-service.onrender.com` in your
frontend's build environment (Render static site env vars, or `.env.production`).

---

## Ongoing operations

### Switching counties

Change `ACTIVE_COUNTY` in the Render dashboard env vars → redeploy.
No code changes required.

### Uploading new raster data

1. Upload new TIFs to S3 following the folder structure above
2. Hit the reload endpoint (no restart needed):
```bash
curl -X POST https://your-service.onrender.com/admin/reload
```

### Cold start behaviour

Render free/starter tier spins down after inactivity. On the next request:
- S3 sync runs first (~10–30 seconds depending on file sizes)
- Then layers load into memory
- Health check passes and traffic is served

Use Render's **paid tier** or a **cron job pinging `/health`** every 10 minutes
to keep the service warm if cold starts are a problem.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `layers_loaded: 0` | S3 credentials wrong or bucket name mismatch | Check env vars in Render dashboard |
| `boundary_available: false` | `kitui/boundary/` folder empty or wrong filename | File must be `kitui_boundary.gpkg` |
| `status: degraded` | Some layers missing | Check `/health` for which layers loaded |
| 500 on `/analyze` | Constraint mask missing | Check `kitui/preprocessed/kitui_constraints_mask.tif` exists in S3 |
| Frontend CORS error | API URL wrong in React | Set `REACT_APP_API_URL` env var |
| Render deploy fails | Wrong start command | Must be `uvicorn src.api:app ...` not `uvicorn api:app ...` |