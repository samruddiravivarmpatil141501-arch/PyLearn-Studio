# PyLearn Studio
### Interactive Python Learning Platform — Flask Edition

A dark developer-style Python learning platform designed for a college academic project.

## Included
- Flask + SQLite
- Secure password hashing
- Login / registration / logout
- Dark full-window UI
- 7 learning modules
- Topic matter + concise sub-concepts
- Syntax separated from examples
- Generic sample data (no personal learner data)
- One embedded YouTube video per module, plus a YouTube search link for every topic
- Practice Lab with local Python execution and timeout
- Save practice code
- A separate 7-question quiz for each module (pass mark 70% = 5/7); option order is shuffled on every attempt
- Progress dashboard
- Certificate unlocks only after ALL topics are completed AND ALL 7 module quizzes are passed
- Print-friendly certificate
- Responsive layout

## Run in VS Code / Windows

1. Open this folder in VS Code.
2. Open Terminal.
3. Create a virtual environment:

```powershell
py -m venv .venv
```

4. Activate it:

```powershell
.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use:

```powershell
.venv\Scripts\activate.bat
```

5. Install dependencies:

```powershell
py -m pip install -r requirements.txt
```

6. Start the application:

```powershell
py app.py
```

7. Open:

http://127.0.0.1:5000

The SQLite database `pylearn.db` and a `secret.key` file are created automatically on first run (or set the `PYLEARN_SECRET` environment variable).

## Important Practice Lab note

The Practice Lab is intended for a local academic demonstration. It runs code in a short-lived subprocess with a 4-second limit. Only math, random, datetime, string, collections, itertools, json, csv, sqlite3 (":memory:" or a plain file name) and statistics can be imported; memory, CPU and file size are limited on Linux/macOS; open() works only with plain file names inside a temporary folder; classes, lambda and input() are supported (type input values in the Program input box). Do not deploy this code runner as a public multi-user code execution service without a proper sandbox/container isolation layer.

## Content customization

Lessons are in `content/modules.json` (one example per topic, one `video_id` per module) and quiz questions are in `content/quizzes.json` (the FIRST option of each question is the correct one; the app shuffles them). Edit these files, not `app.py`.

## Certificate

The certificate is an in-browser HTML/CSS certificate and can be printed to PDF using the browser print dialog. It is labeled as a PyLearn Studio certificate of completion; do not represent it as an officially issued university certificate without institutional authorization.

## Suggested next enhancements

- Add a final assessment.
- Add admin/content management.
- Add charts with Chart.js.
- Add certificate verification page.

### Video resources
Each module has one embedded YouTube lesson (`video_id` in `content/modules.json`) and every topic has a YouTube search link. Open each module once and check the video plays; replace the `video_id` if a video was removed or blocks embedding.

### Security notes
All forms and API calls use a CSRF token. Set `PYLEARN_DEBUG=1` only while developing (debug mode is off by default).
