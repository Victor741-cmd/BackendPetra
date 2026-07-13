from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.models import crm
from app.routes.advisors import router as advisors_router
from app.routes.auth import router as auth_router
from app.routes.calls import router as calls_router
from app.routes.conversations import router as conversations_router
from app.routes.users import router as users_router
from app.routes.whatsapp import router as whatsapp_router
from app.routes.media import router as media_router

app = FastAPI(title="WhatsApp CRM API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(whatsapp_router)
app.include_router(advisors_router)
app.include_router(conversations_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(calls_router)
app.include_router(media_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/dev/create-tables")
def create_tables():
    Base.metadata.create_all(bind=engine)
    return {"status": "tables_created"}