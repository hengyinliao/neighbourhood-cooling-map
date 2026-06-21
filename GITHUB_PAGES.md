# Publish To GitHub Pages

Recommended repository name:

```text
neighbourhood-cooling-map
```

With your GitHub username, the public map URL would be:

```text
https://hengyinliao.github.io/neighbourhood-cooling-map/
```

## Local Files

The public website lives in:

```text
docs/index.html
```

The map title is:

```text
Our Neighbourhood Cooling Map
```

Whenever you update data or resident feedback, rerun:

```powershell
python scripts/build_existing_resources.py
```

That command updates both:

- `outputs/cooling_resources_map.html` for local preview.
- `docs/index.html` for GitHub Pages.

## First-Time GitHub Setup

Create a new empty GitHub repository named `neighbourhood-cooling-map`, then run
these commands from this project folder:

```powershell
git init
git branch -M main
git add .
git commit -m "Create neighbourhood cooling map website"
git remote add origin https://github.com/hengyinliao/neighbourhood-cooling-map.git
git push -u origin main
```

## Turn On GitHub Pages

On GitHub:

1. Open `https://github.com/hengyinliao/neighbourhood-cooling-map`.
2. Go to `Settings`.
3. Go to `Pages`.
4. Under `Build and deployment`, choose `Deploy from a branch`.
5. Select branch `main`.
6. Select folder `/docs`.
7. Click `Save`.

After GitHub finishes publishing, residents can open:

```text
https://hengyinliao.github.io/neighbourhood-cooling-map/
```

## Updating Later

After editing `data/resident_input_template.csv` or the Python script:

```powershell
python scripts/build_existing_resources.py
git add .
git commit -m "Update cooling map"
git push
```

GitHub Pages will update automatically after the push.
