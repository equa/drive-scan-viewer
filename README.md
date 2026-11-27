# NAS Usage Browser

A small web app for browsing [ncdu](https://dev.yorhel.nl/ncdu) scan results of the shared NAS,
in the style of GNOME Disk Usage Analyzer (baobab): a folder list next to a ring chart.
It does not scan anything itself. It reads ncdu JSON exports from `data/`.

## Making scans

```sh
nice -19 ncdu -e -x -o /path/to/data/users-username.json /<drive path>/Users/username   # one scan per directory works fine
nice -19 ncdu -e -x -o- /<drive path>/Projects | gzip > /path/to/data/projects.json.gz # compressed is also supported
```

`tools/scan-users.py [--max-dirs N | --as-one] [ROOT] [DATA_DIR]` runs one scan per sub-folder of `ROOT` (for example
every user directory) and logs to `DATA_DIR/scan-users.log`. Each file is renamed into place only when its
scan is done. `--max-dirs N` scans only N randomly chosen sub-folders, for a trial run.
`--as-one` scans `ROOT` itself into a single file named after it, for example to re-scan one user:
it gets the same file name as in a full run, so it replaces that user's previous scan.

Any `*.json` or `*.json.gz` file under `data/` (subfolders included) appears in the scan picker.
You can select several at once: they are shown side by side under an "All selected scans" root.
A changed file is reloaded automatically the next time someone opens it.

## Archiving and trashing

Create the two folders `0_ARCHIVE___` and `0_TRASH___` at the top of the drive (or of whatever level should
count as the top). `tools/g-drive-cleanup.py` finds them by searching upwards from the current directory and
moves things into them, keeping their path below the top. Because the paths are relative to the top, the same
script works on Linux (`/<drive path>`) and on Windows (`G:\`):

```sh
cd <top>/Users/x/Projects/A
g-drive-cleanup.py --archive cfd docs    # -> <top>/0_ARCHIVE___/Users/x/Projects/A/{cfd,docs}
g-drive-cleanup.py --trash old-runs      # -> <top>/0_TRASH___/Users/x/Projects/A/old-runs
g-drive-cleanup.py --restore cfd         # back again (--from archive|trash if it is in both)
```

`-n` shows the moves without making them. An existing target is an error unless `--keep-both` is given, which
adds a timestamp to the name. Moves are plain renames and fail rather than copy across drives. Each move is
logged to `cleanup.log` in the archive or trash folder.

`tools/g-drive-cleanup.ps1` does the same for Windows users without Python, with PowerShell-style options
(`-Archive`, `-Trash`, `-Restore`, `-From`, `-KeepBoth`, `-DryRun`):

```powershell
cd G:\Users\x\Projects\A
powershell -ExecutionPolicy Bypass -File \path\to\g-drive-cleanup.ps1 -Archive cfd docs
```

`-ExecutionPolicy Bypass` is only needed where script execution is restricted (the default on Windows);
`Get-Help .\g-drive-cleanup.ps1 -Full` shows the options.

## Running

```sh
podman compose up -d --build     # or: podman-compose up -d --build
# open http://<host>:8080
```

Without compose:

```sh
podman build -t nasbrowser -f Containerfile .
podman run -d --name nasbrowser -p 8080:8080 -v ./data:/data:ro,z nasbrowser
```

Locally without a container (Python ≥ 3.10, stdlib only):

```sh
DATA_DIR=data python3 app/server.py
```

## Features

- **Folders**: expandable tree with share of the current folder, size, item count, and the newest
  modification anywhere inside. Click a name to open it. Hovering a row highlights its ring segment.
- **Ring chart**: click a segment to open it, click the centre to go up. Tiny entries are merged into
  grey "N smaller items" segments. Folders are saturated and files are muted.
- **Colour by age**: the *Colour: Age* toggle colours every slice by the newest change anywhere
  inside it (< 1, 1–2, 2–4, 4–8, 8–10, > 10 years). A dark red slice means nothing in it has been
  touched for over 10 years. The legend shows how much space each age bucket holds. Hover an entry
  to dim the other slices.
- **File types**: space per extension. Click one to list its largest files.
- **Age**: space per year of last modification.
- **Cleanup**: largest files (filter by age, extension, or name) and *untouched folders*, the
  top-most folders with nothing modified for N years. These are the best candidates for archiving.
- Sizes are disk usage (what `du` reports). Ring depth is adjustable, and URLs are bookmarkable (`#scans=…&at=…`).
- "Copy path" for the current folder. Set `PATH_MAP` so that it copies a path colleagues recognise,
  for example `PATH_MAP=/<drive path>=\\nas\q` turns `/<drive path>/Users/x` into `\\nas\q\Users\x`.

## Configuration

| Variable     | Default  | Meaning                                              |
|--------------|----------|------------------------------------------------------|
| `DATA_DIR`   | `/data`  | Directory with ncdu exports                          |
| `PORT`       | `8080`   | Listen port                                          |
| `MAX_LOADED` | `0`      | Cap on parsed scans in memory (LRU), 0 = all         |
| `PRELOAD`    | `1`      | Parse the scans at startup                           |
| `PATH_MAP`   | *(none)* | `from=to[;from=to…]` display rewrite for copied paths |

## Memory and speed

The server streams the JSON and packs every entry into flat arrays.
A real-world scan of 120k entries parses in 0.4 s. A synthetic 1M-entry scan (89 MB JSON) took 2 s and
~100 MB of RAM. Split very large shares into several scans, for example one per user
directory. All scans are shown by default, so the host needs RAM for all of them together.
