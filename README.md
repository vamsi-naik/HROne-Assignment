# Employee Attendance & Analytics API

A Python API built with FastAPI and MongoDB for employee attendance, punch-in/punch-out, attendance corrections with an audit history, and attendance analytics.

## Requirements

- Python 3.11 or newer
- MongoDB 6.0 or newer
- Git

## Setup (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:MONGO_URI="mongodb://localhost:27017"
$env:MONGO_DB="attendance_db"
python sample_seed.py
uvicorn app.main:app --reload --port 8000
```

For macOS/Linux, activate the virtual environment with `source .venv/bin/activate` and set environment variables with `export MONGO_URI=...` and `export MONGO_DB=attendance_db`.

Open `http://127.0.0.1:8000/docs` for the interactive API documentation. Do not commit credentials, `.env` files, virtual environments, or database dumps containing real personal data.

## Project structure

- `app/main.py` — API routes, validation, business rules, and MongoDB queries/aggregations
- `requirements.txt` — Python dependencies
- `REVIEW.md` — review of the supplied starter code
- `DECISIONS.md` — implementation design choices and trade-offs
- `sample_seed.py` — loads the supplied sample records into MongoDB
- `sample_data/` — sample employee and attendance records for local testing

## Before submission

Run the service against MongoDB 6.0+ and verify every route and response against the API contract supplied with the assignment. Test invalid inputs, duplicate/concurrent punch-ins, overnight shifts, correction audit history, pagination, analytics edge cases, and explain output. Record any incomplete functionality honestly; do not claim tests passed unless you ran them.
