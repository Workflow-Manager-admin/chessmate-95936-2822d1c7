"""
Backend API for Chessmate: Chess game server with user auth, move handling, AI play, game
history & results via Supabase.

FastAPI docs: automatic at /docs and /redoc
Supabase: used for both user authentication & Postgres data operations.
Chess AI: stubbed, actual integration would call an engine.

Env vars required (see README/.env):
- SUPABASE_URL
- SUPABASE_KEY
- SUPABASE_DB_URL

Run this app: uvicorn src.api.main:app --reload
"""

import os
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import httpx
import uuid

# --- Configuration: Supabase ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
SUPABASE_AUTH_URL = f"{SUPABASE_URL}/auth/v1"
SUPABASE_REST_URL = f"{SUPABASE_URL}/rest/v1"

# --- CORS/Swagger Tags ---
openapi_tags = [
    {"name": "auth", "description": "User authentication"},
    {"name": "users", "description": "User management"},
    {"name": "games", "description": "Game registration and status"},
    {"name": "moves", "description": "Submit moves and view move history"},
    {"name": "ai", "description": "Play against AI"},
]

app = FastAPI(
    title="Chessmate Backend API",
    description="Chess backend for playing with AI, managing users, and storing chess game "
    "data. Documentation included.",
    version="1.0.0",
    openapi_tags=openapi_tags
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


# --- Models ---

# PUBLIC_INTERFACE
class UserCreate(BaseModel):
    """Model for user registration."""
    email: str = Field(..., description="User email address")
    password: str = Field(..., description="User password")


# PUBLIC_INTERFACE
class TokenResponse(BaseModel):
    """Response model for Auth tokens."""
    access_token: str
    token_type: str = "bearer"


# PUBLIC_INTERFACE
class GameCreate(BaseModel):
    """Model for creating a new game."""
    opponent_id: Optional[str] = Field(
        None, description="User ID of chess opponent, or None for AI"
    )
    is_ai_game: bool = Field(False, description="Play against AI if true")
    color: Optional[str] = Field("white", description='Color for user: "white" or "black"')


# PUBLIC_INTERFACE
class GameOut(BaseModel):
    """Game state return model."""
    game_id: str
    fen: str
    creation_time: str
    status: str
    players: Dict[str, str]  # { "white": user_id, "black": user_id or null }
    winner: Optional[str] = None


# PUBLIC_INTERFACE
class MoveRequest(BaseModel):
    """Model for submitting a chess move."""
    move: str = Field(..., description="Move in UCI or algebraic notation")
    game_id: str = Field(..., description="Game unique ID")


# PUBLIC_INTERFACE
class MoveHistoryItem(BaseModel):
    """Model for a single move in move history."""
    move_number: int
    move: str
    player: str
    fen: str


# PUBLIC_INTERFACE
class AIPlayRequest(BaseModel):
    """Request to make a move as/against the AI."""
    game_id: str
    difficulty: Optional[int] = 1


# Helper: Proxy to Supabase REST (POST, PATCH, DELETE)
async def supabase_request(
    table: str,
    method: str = "GET",
    params: dict = None,
    data: dict = None,
    token: str = None,
    id_filter: str = None
):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = f"{SUPABASE_REST_URL}/{table}"
    if id_filter:
        url += f"?id=eq.{id_filter}"
    async with httpx.AsyncClient() as client:
        if method == "GET":
            resp = await client.get(url, headers=headers, params=params)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=data)
        elif method == "PATCH":
            resp = await client.patch(url, headers=headers, json=data)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError("Unsupported HTTP method")
    if not resp.is_success:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()


# --- Auth routes ---

# PUBLIC_INTERFACE
@app.post(
    "/auth/register",
    summary="Register a new user",
    tags=["auth"],
    response_model=TokenResponse
)
async def register(user: UserCreate):
    """
    Registers a new user in Supabase Auth.
    Returns access token upon success.
    """
    payload = {"email": user.email, "password": user.password}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_AUTH_URL}/signup", json=payload, headers={"apikey": SUPABASE_KEY}
        )
    if resp.is_success:
        out = resp.json()
        return TokenResponse(access_token=out["access_token"])
    detail = resp.json().get("msg") or resp.text
    raise HTTPException(status_code=resp.status_code, detail=detail)


# PUBLIC_INTERFACE
@app.post(
    "/auth/token",
    summary="User Login",
    tags=["auth"],
    response_model=TokenResponse
)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Logs in a user using Supabase and returns a bearer token.
    """
    payload = {"email": form_data.username, "password": form_data.password}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{SUPABASE_AUTH_URL}/token",
            data=payload,
            headers={
                "apikey": SUPABASE_KEY,
                "Content-Type": "application/x-www-form-urlencoded"
            }
        )
    if resp.is_success:
        out = resp.json()
        return TokenResponse(access_token=out["access_token"])
    detail = resp.json().get("msg") or resp.text
    raise HTTPException(status_code=resp.status_code, detail=detail)


# PUBLIC_INTERFACE
@app.get("/users/me", summary="Get my user info", tags=["users"])
async def get_me(token: str = Depends(oauth2_scheme)):
    """
    Gets current user's info from Supabase.
    """
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{SUPABASE_AUTH_URL}/user", headers=headers)
    if not resp.is_success:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )
    return resp.json()


# PUBLIC_INTERFACE
@app.get("/users/", summary="List users (demo)", tags=["users"])
async def list_users(token: str = Depends(oauth2_scheme)):
    """
    Returns list of users via Supabase Users table (not auth).
    """
    # Assume a 'users' table for demo (custom tables managed via REST)
    return await supabase_request("users", token=token)


# --- Games ---

# PUBLIC_INTERFACE
@app.post(
    "/games/",
    summary="Create new chess game",
    tags=["games"],
    response_model=GameOut
)
async def create_game(game: GameCreate, token: str = Depends(oauth2_scheme)):
    """
    Creates a new chess game, against another user or AI.
    Returns game_id and initial state.
    """
    user = await get_me(token)
    game_id = str(uuid.uuid4())
    initial_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    color = "white" if game.color == "white" else "black"
    players = {
        color: user["id"],
        ("black" if game.color == "white" else "white"): (
            None if game.is_ai_game else game.opponent_id
        )
    }
    game_record = {
        "id": game_id,
        "fen": initial_fen,
        "status": "waiting" if not game.is_ai_game else "active",
        "players": players,
        "winner": None,
    }
    # Store game in rest table 'games'
    await supabase_request("games", method="POST", data=game_record, token=token)
    return GameOut(
        game_id=game_id,
        fen=initial_fen,
        creation_time="just now",
        status=game_record["status"],
        players=players,
        winner=None
    )


# PUBLIC_INTERFACE
@app.get(
    "/games/{game_id}",
    summary="Get game state",
    tags=["games"],
    response_model=GameOut
)
async def get_game(game_id: str, token: str = Depends(oauth2_scheme)):
    """
    Retrieve current state (including board FEN, status etc) for a game.
    """
    records = await supabase_request("games", id_filter=game_id, token=token)
    if not records:
        raise HTTPException(status_code=404, detail="Game not found")
    rec = records[0]
    return GameOut(
        game_id=rec["id"],
        fen=rec["fen"],
        creation_time=rec.get("created_at", ""),
        status=rec.get("status", ""),
        players=rec.get("players", {}),
        winner=rec.get("winner", None)
    )


# PUBLIC_INTERFACE
@app.get(
    "/games/{game_id}/history",
    summary="Get move history",
    tags=["moves"],
    response_model=List[MoveHistoryItem]
)
async def get_move_history(game_id: str, token: str = Depends(oauth2_scheme)):
    """
    Get all moves for a specific game, including FENs at each step.
    """
    moves = await supabase_request(
        "moves",
        params={"game_id": f"eq.{game_id}"},
        token=token
    )
    return [MoveHistoryItem(**item) for item in moves]


# --- Move Submission and AI Play ---

# Dummy chess move validation (use `python-chess` library in real scenario)
def _is_move_legal_stub(fen: str, move: str) -> bool:
    # Always accept moves (replace by actual logic in prod)
    return True


# Dummy AI move (replace with actual engine integration)
def _generate_ai_move_stub(fen: str, level: int = 1) -> str:
    # Return a made-up plausible move for demonstration
    return "e2e4"


# PUBLIC_INTERFACE
@app.post(
    "/games/{game_id}/move",
    summary="Submit move",
    tags=["moves"]
)
async def submit_move(
    game_id: str,
    req: MoveRequest,
    token: str = Depends(oauth2_scheme)
):
    """
    Submit a chess move for a game. Validates the move.
    Responds with updated game state and move history.
    """
    # Fetch game
    games = await supabase_request("games", id_filter=game_id, token=token)
    if not games:
        raise HTTPException(404, "Game not found")
    game = games[0]
    fen = game["fen"]

    # Validate move and update state
    if not _is_move_legal_stub(fen, req.move):
        raise HTTPException(400, "Illegal move")

    # Update new FEN (stub: no real chess update)
    new_fen = fen  # Use chess library for real update!
    move_record = {
        "game_id": game_id,
        "move": req.move,
        "player": "user",  # Enhance: infer actual player
        "fen": new_fen,
        "move_number": 1,  # Enhance: determine move number
    }
    await supabase_request(
        "moves",
        method="POST",
        data=move_record,
        token=token
    )
    await supabase_request(
        "games",
        method="PATCH",
        id_filter=game_id,
        data={"fen": new_fen},
        token=token
    )
    # Return updated move history
    moves = await supabase_request(
        "moves",
        params={"game_id": f"eq.{game_id}"},
        token=token
    )
    return {"game_id": game_id, "fen": new_fen, "moves": moves}


# PUBLIC_INTERFACE
@app.post(
    "/games/{game_id}/ai-move",
    summary="Let AI play",
    tags=["ai"]
)
async def ai_move(
    game_id: str,
    req: AIPlayRequest,
    token: str = Depends(oauth2_scheme)
):
    """
    Generate and execute an AI move for this game.
    Returns updated FEN and AI's move.
    """
    # Fetch game state
    games = await supabase_request("games", id_filter=game_id, token=token)
    if not games:
        raise HTTPException(404, "Game not found")
    game = games[0]
    fen = game["fen"]
    ai_move = _generate_ai_move_stub(fen, level=req.difficulty)
    # Store AI move as 'player': 'ai'
    move_record = {
        "game_id": game_id,
        "move": ai_move,
        "player": "ai",
        "fen": fen,
        "move_number": 1,
    }
    await supabase_request(
        "moves",
        method="POST",
        data=move_record,
        token=token
    )
    # In a real scenario, update new FEN and patch game state
    await supabase_request(
        "games",
        method="PATCH",
        id_filter=game_id,
        data={"fen": fen},
        token=token
    )
    return {"ai_move": ai_move, "fen": fen}


# --- Game Results ---

# PUBLIC_INTERFACE
@app.get(
    "/games/{game_id}/result",
    summary="Get game result",
    tags=["games"]
)
async def game_result(game_id: str, token: str = Depends(oauth2_scheme)):
    """
    Returns result for the chess game: winner, or ongoing.
    """
    games = await supabase_request("games", id_filter=game_id, token=token)
    if not games:
        raise HTTPException(404, "Game not found")
    game = games[0]
    return {"winner": game.get("winner"), "status": game.get("status")}


# --- Health/Utils ---


@app.get("/", summary="Healthcheck", tags=["users"])
def health_check():
    """Simple health check for the API."""
    return {"message": "Healthy"}


@app.get(
    "/docs/websocket",
    tags=["users"],
    summary="How to use Websocket",
    description="No websocket endpoints available; only REST endpoints are supported for moves and game state."
)
def websocket_usage_doc():
    """Explains (absence of) websocket support."""
    info_text = (
        "Only REST endpoints are available. "
        "Use REST for all move, state, and authentication operations."
    )
    return {
        "info": info_text
    }


# --- Custom OpenAPI: Add security info ---
def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    # Add auth security schemes
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer"
        }
    }
    for path in openapi_schema["paths"].values():
        for op in path.values():
            if "security" not in op:
                op["security"] = [{"BearerAuth": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi
