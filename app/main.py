# main.py
# FastAPI() — creates the app
# CORSMiddleware — allows SwiftUIto talk to backend without getting blocked
# include_router — plugs in our game and leaderboard routes
# The / endpoint — a simple health check so we can confirm the server is running


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import games, leaderboard

app = FastAPI(title="Blipz API", description="API for Blipz game", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(games.router, prefix="/games", tags=["games"])
app.include_router(leaderboard.router, prefix="/leaderboard", tags=["leaderboard"])

@app.get("/")
def root():
    return {"message": "Blipz API is running!"}



