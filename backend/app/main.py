from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware

from app.db import get_conn
from app.auth import verify_password

app = FastAPI()

# IMPORTANT: change to a strong random value in production
app.add_middleware(SessionMiddleware, secret_key="CHANGE_ME_TO_A_LONG_RANDOM_SECRET")

# If you use Vite proxy, CORS is usually not needed.
# If you are NOT using proxy, keep this.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8080", "http://127.0.0.1:8080"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _require_user_id(request: Request) -> int:
    uid = request.session.get("user_id")
    if not uid:
        raise KeyError("not authenticated")
    return int(uid)

def _is_admin(role: str | None) -> bool:
    return (role or "").lower() == "admin"

@app.get("/api/health")
def health():
    return {"ok": True}

# -------------------------
# AUTH
# -------------------------
@app.post("/api/auth/login")
async def login(request: Request):
    data = await request.json()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return JSONResponse({"error": "Email and password required"}, status_code=400)

    with get_conn() as conn:
        user = conn.execute(
            "SELECT id, email, password_hash, role, name FROM users WHERE email = ?",
            (email,),
        ).fetchone()

    if not user or not verify_password(password, user["password_hash"]):
        return JSONResponse({"error": "Invalid email or password"}, status_code=401)

    request.session["user_id"] = int(user["id"])

    return {
        "ok": True,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "role": user["role"],
            "name": user["name"],
        },
    }

@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}

@app.get("/api/auth/me")
def me(request: Request):
    try:
        uid = _require_user_id(request)
    except KeyError:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    with get_conn() as conn:
        user = conn.execute(
            "SELECT id, email, role, name FROM users WHERE id = ?",
            (uid,),
        ).fetchone()

    if not user:
        request.session.clear()
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    return {"user": dict(user)}

# -------------------------
# SIMULATIONS
# -------------------------
@app.get("/api/simulations")
def list_simulations(request: Request):
    try:
        uid = _require_user_id(request)
    except KeyError:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
              s.id,
              s.name,
              s.type,
              COALESCE(s.status, 'running') AS status,
              COALESCE(s.progress, 0) AS progress,
              COALESCE(s.participants, 0) AS participants,
              COALESCE(s.started_at, '—') AS startedAt,
              COALESCE(s.estimated_end, '—') AS estimatedEnd
            FROM user_simulations us
            JOIN simulations s ON s.id = us.simulation_id
            WHERE us.user_id = ?
            ORDER BY s.id DESC
            """,
            (uid,),
        ).fetchall()

    return {"simulations": [dict(r) for r in rows]}

@app.post("/api/simulations")
async def create_simulation(request: Request):
    try:
        uid = _require_user_id(request)
    except KeyError:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    data = await request.json()
    name = (data.get("name") or "").strip()
    sim_type = (data.get("type") or "phishing").strip()

    if not name:
        return JSONResponse({"error": "Simulation name is required"}, status_code=400)

    with get_conn() as conn:
        # Create simulation
        cur = conn.execute(
            """
            INSERT INTO simulations (name, type, status, progress, participants, started_at, estimated_end)
            VALUES (?, ?, 'running', 0, 0, datetime('now'), date('now', '+7 day'))
            """,
            (name, sim_type),
        )
        sim_id = cur.lastrowid

        # Assign to creator (so it shows up immediately)
        conn.execute(
            """
            INSERT OR IGNORE INTO user_simulations (user_id, simulation_id, role)
            VALUES (?, ?, 'admin')
            """,
            (uid, sim_id),
        )

        conn.commit()

        created = conn.execute(
            """
            SELECT id, name, type,
                   COALESCE(status, 'running') AS status,
                   COALESCE(progress, 0) AS progress,
                   COALESCE(participants, 0) AS participants,
                   COALESCE(started_at, '—') AS startedAt,
                   COALESCE(estimated_end, '—') AS estimatedEnd
            FROM simulations WHERE id = ?
            """,
            (sim_id,),
        ).fetchone()

    return {"ok": True, "simulation": dict(created)}

@app.delete("/api/simulations/{simulation_id}")
def delete_simulation(simulation_id: int, request: Request):
    try:
        uid = _require_user_id(request)
    except KeyError:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    with get_conn() as conn:
        # Check user has access to this simulation and whether they’re admin for it
        membership = conn.execute(
            """
            SELECT us.role, u.role as user_role
            FROM user_simulations us
            JOIN users u ON u.id = us.user_id
            WHERE us.user_id = ? AND us.simulation_id = ?
            """,
            (uid, simulation_id),
        ).fetchone()

        if not membership:
            return JSONResponse({"error": "Not found"}, status_code=404)

        # Allow delete if simulation-role admin OR global user role admin
        allowed = (membership["role"] == "admin") or _is_admin(membership["user_role"])
        if not allowed:
            return JSONResponse({"error": "Forbidden"}, status_code=403)

        conn.execute("DELETE FROM simulations WHERE id = ?", (simulation_id,))
        conn.execute("DELETE FROM user_simulations WHERE simulation_id = ?", (simulation_id,))
        conn.commit()

    return {"ok": True}
