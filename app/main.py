# main.py
# FastAPI() — creates the app
# CORSMiddleware — allows SwiftUIto talk to backend without getting blocked
# include_router — plugs in our game and leaderboard routes
# The / endpoint — a simple health check so we can confirm the server is running


from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import games, leaderboard, friends, users
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Blipz API", description="API for Blipz game", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(games.router, prefix="/games", tags=["games"])
app.include_router(leaderboard.router, prefix="/leaderboard", tags=["leaderboard"])
app.include_router(friends.router, prefix="/friends", tags=["friends"])
app.include_router(users.router, prefix="/users", tags=["users"])

@app.get("/")
def root():
    return {"message": "Blipz API is running!"}



