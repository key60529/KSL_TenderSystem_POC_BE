# Tender System — Backend

A FastAPI backend powering the Tender System POC. It manages users, projects, tenders, file attachments, and integrates with a Dify AI service to generate tender drafts.

## Tech stack

- **FastAPI** — REST API framework
- **SQLAlchemy** — ORM
- **PostgreSQL** — Primary database
- **python-jose / passlib** — JWT authentication & password hashing
- **Uvicorn** — ASGI server
- **Dify** — External AI service for tender draft generation

## Project structure

```text
backend/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── app/
    ├── main.py          # App entry point, mounts routes, startup logic
    ├── auth.py          # JWT creation & verification helpers
    ├── database.py      # SQLAlchemy engine & session setup
    ├── models.py        # ORM table definitions
    ├── seed.py          # (optional) Database seeding script
    ├── routes/
    │   ├── auth.py      # /auth/register, /auth/login
    │   ├── projects.py  # /projects CRUD (auth-protected)
    │   └── tenders.py   # /tenders draft generation & file upload
    └── services/
        └── dify_service.py  # Dify AI API integration
```

## Data models

| Table                | Purpose                                                   |
| -------------------- | --------------------------------------------------------- |
| `users`              | Registered users                                          |
| `projects`           | Projects owned by a user, stores master requirements JSON |
| `tenders`            | Tender bids linked to a project                           |
| `tender_statuses`    | Lookup table: Pending / Verified / Rejected               |
| `tender_attachments` | Files uploaded and linked to a tender                     |

## API overview

### Authentication (`/auth`)

| Method | Endpoint         | Auth required | Description            |
| ------ | ---------------- | ------------- | ---------------------- |
| POST   | `/auth/register` | No            | Register a new user    |
| POST   | `/auth/login`    | No            | Get a JWT access token |

### Projects (`/projects`)

All project endpoints require a valid JWT (`Authorization: Bearer <token>`).

| Method | Endpoint         | Description                         |
| ------ | ---------------- | ----------------------------------- |
| POST   | `/projects/`     | Create a new project                |
| PUT    | `/projects/{id}` | Update project details/requirements |
| DELETE | `/projects/{id}` | Delete a project (owner only)       |

### Tenders

| Method | Endpoint                           | Auth required | Description                                                   |
| ------ | ---------------------------------- | ------------- | ------------------------------------------------------------- |
| POST   | `/submit`                          | No            | Quick-submit a tender                                         |
| GET    | `/tenders`                         | No            | List all tenders                                              |
| GET    | `/tenders/{id}`                    | No            | Get a single tender with attachments                          |
| PUT    | `/tenders/{id}`                    | No            | Update tender fields                                          |
| PATCH  | `/tenders/{id}/verify`             | No            | Mark a tender as Verified                                     |
| PATCH  | `/tenders/{id}/update-status`      | No            | Set status: 1=Pending, 2=Verified, 3=Rejected                 |
| POST   | `/tenders/{id}/upload-document`    | No            | Upload & link a document to a tender                          |
| POST   | `/tenders/{id}/attachments`        | No            | Add an attachment record                                      |
| DELETE | `/attachments/{id}`                | No            | Remove an attachment                                          |
| POST   | `/tenders/{id}/generate-draft`     | No            | Ask Dify AI to generate a draft                               |
| POST   | `/tenders/{id}/upload-requirement` | Yes           | Upload a requirement file for AI processing (background task) |

### File downloads

Uploaded files are served statically at:

```
GET /download/{filename}
```

## Environment variables

| Variable        | Default                                                       | Description                  |
| --------------- | ------------------------------------------------------------- | ---------------------------- |
| `DATABASE_URL`  | `postgresql://postgres:admin123@localhost:5432/tender_system` | PostgreSQL connection string |
| `DIFY_API_KEY`  | `app-UnhDDkWMmnpIj70EcEVfkomo`                                | Dify AI service API key      |
| `SEED_USERNAME` | `admin_user`                                                  | POC dummy login username     |
| `SEED_PASSWORD` | `password123`                                                 | POC dummy login password     |

> ⚠️ The `SECRET_KEY` used for JWT signing is currently hardcoded in `app/auth.py`. **Replace it with a proper secret environment variable before deploying to production.**

## Running locally (without Docker)

### Prerequisites

- Python 3.12+
- A running PostgreSQL instance

### Setup

```bash
cd backend

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variable (or let it fall back to the default)
export DATABASE_URL=postgresql://postgres:admin123@localhost:5432/tender_system

# Run the server
uvicorn app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.  
Interactive docs (Swagger UI) at `http://localhost:8000/docs`.

## Running with Docker Compose (recommended)

The `docker-compose.yml` starts both the **PostgreSQL** database and the **FastAPI** app in one command.

```bash
cd backend
docker-compose up --build
```

| Service | Internal port | Exposed port |
| ------- | ------------- | ------------ |
| `db`    | 5432          | 5433         |
| `web`   | 8000          | 55555        |

The API will be available at `http://localhost:55555`.  
Interactive docs at `http://localhost:55555/docs`.

> The `uploaded_tenders/` folder is volume-mounted so generated files persist across container restarts.

## Running with Docker only (no Compose)

```bash
cd backend

# Build the image
docker build -t tender-backend .

# Run with an external database URL
docker run -p 8000:8000 \
  -e DATABASE_URL=postgresql://postgres:admin123@host.docker.internal:5432/tender_system \
  tender-backend
```

## Dify AI integration

The Dify service (`app/services/dify_service.py`) connects to a self-hosted Dify instance to:

1. **Generate tender drafts** — `POST /tenders/{id}/generate-draft` sends a prompt to Dify and saves the returned text as a `.txt` file under `uploaded_tenders/`.
2. **Process uploaded requirements** — `POST /tenders/{id}/upload-requirement` runs as a **background task**, so the user gets an immediate response while Dify processes the file asynchronously.

Configure the Dify endpoint by updating `DIFY_URL` in `dify_service.py` or by setting `DIFY_API_KEY` as an environment variable.

## Startup behaviour

On first boot the app auto-creates all database tables and seeds the `tender_statuses` lookup table with three rows:

| ID  | Name     | Description    |
| --- | -------- | -------------- |
| 1   | Pending  | Waiting for AI |
| 2   | Verified | AI Approved    |
| 3   | Rejected | AI Rejected    |

## Notes & known issues

- `SECRET_KEY` in `app/auth.py` must be moved to an environment variable for any production deployment.
- The Dify base URL (`192.168.8.162`) is currently hardcoded in both `main.py` and `dify_service.py` — centralise it into an environment variable when the Dify host changes.
- `upload-requirement` saves files to `temp_storage/` — ensure this directory exists before running without Docker.
- The `seed.py` file exists but is not called automatically; run it manually if additional seed data is needed.
