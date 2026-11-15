# Installing G-reality

## No install: the website

Open the public website in Chrome, Edge or Firefox (WebGL2, a discrete or recent integrated GPU) and press
**Play the sample world**. Click to look; WASD move, Shift sprint, Space jump, Esc release the mouse. The sample is
read-only: generating worlds and making changes needs the Windows companion below.

## Windows companion

**This is not zero-setup.** The companion stores worlds in **your own PostgreSQL database**. A free
[Neon](https://neon.tech) project works: *New project → Connect → copy the connection string* (`postgresql://…`).
A [Gemini API key](https://aistudio.google.com/apikey) is optional; without one the built-in keyword interpreter
designs worlds (they follow the description less closely). You also need internet access on first launch.

### Recommended: install with pip into its own environment (works with Smart App Control)

Open **Windows PowerShell** or **Terminal** (PowerShell tab). If Python 3.12 or newer is not installed, run this
first, then close and reopen the terminal:

```powershell
winget install -e --id Python.Python.3.12
```

Install G-reality into a private environment (your other Python packages are not touched), add a desktop shortcut,
and start it:

```powershell
py -3 -m venv "$env:LOCALAPPDATA\G-reality\env"
& "$env:LOCALAPPDATA\G-reality\env\Scripts\python.exe" -m pip install --only-binary=:all: "https://github.com/No-o-ri/G-reality/releases/download/v1.1.0/greality-1.1.0-py3-none-any.whl"
& "$env:LOCALAPPDATA\G-reality\env\Scripts\python.exe" -m greality --shortcut
& "$env:LOCALAPPDATA\G-reality\env\Scripts\python.exe" -m greality
```

- First launch only: paste the database URL and, optionally, the Gemini key (input is hidden). They are saved to
  `%APPDATA%\G-reality\.env.local` and are only sent to Neon and Gemini.
- The launcher creates the database tables, starts the server on `127.0.0.1:8765` and opens your browser.
- Later, start G-reality with the **G-reality** desktop shortcut (or the last command above).
- While playing, press **T** to describe something new and add it to the current world (needs the Gemini key).
- Updating from an earlier version: run the `pip install` command above again (same environment), then restart
  G-reality. Database changes (e.g. the texture cache in v1.1.0) are applied automatically on start.
- Photo textures (CC0, Poly Haven) are downloaded the first time a world uses them and cached in your database; until
  they arrive (or without internet) surfaces are procedural. Press the backquote key for diagnostics
  ("photo textures x/y").
- Stop with Ctrl+C or by closing the window.

About Smart App Control: Windows blocks script files downloaded from the internet, including the `G-reality.cmd`
in the ZIP. **G-reality itself is not code-signed and is not vetted by Smart App Control.** With this route, the
programs Windows runs are the signed Python interpreter and prebuilt Python packages (FastAPI, psycopg, NumPy, …),
which Smart App Control evaluates as usual; G-reality's own code runs as Python source inside that interpreter.
Nothing needs to be unblocked and no security setting needs to change. Do not turn off Smart App Control.

### Alternative: ZIP with a double-click launcher (only without Smart App Control)

If Smart App Control is **off** on your PC, you can download `G-reality-win64.zip` from the
[v1.1.0 release](https://github.com/No-o-ri/G-reality/releases/tag/v1.1.0), unzip it and double-click
`G-reality.cmd`. It offers to install Python with winget, asks for the same settings, and opens the browser. With
Smart App Control on, Windows blocks `G-reality.cmd` ("files of this type from the internet can be dangerous");
use the pip route instead.

### Troubleshooting

Run these in PowerShell (`$py` is the environment's Python):

```powershell
$py = "$env:LOCALAPPDATA\G-reality\env\Scripts\python.exe"
& $py -m greality --preflight-only     # check requirements without starting
& $py -m greality --port 8770          # use another port
```

- `py` is not recognized, or `py -3` is older than 3.12: install Python with the winget command above and reopen the
  terminal (`py -3.12 -m venv …` selects that version explicitly).
- Change the database URL or key: edit or delete `%APPDATA%\G-reality\.env.local`.
- Uninstall: delete the desktop shortcut and the folders `%LOCALAPPDATA%\G-reality` (environment, data, logs) and
  `%APPDATA%\G-reality` (settings).

The experimental Gaussian far field needs WSL2 and a CUDA PyTorch environment; it is never required.
