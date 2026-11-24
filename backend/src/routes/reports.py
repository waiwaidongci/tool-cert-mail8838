from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from src.config.database import connection
from src.controllers.auth import current
from src.models.identity import User
from src.models.jobs import Publication
from src.models.source import Dataset
from src.utils import views

router = APIRouter(prefix='/reports')


@router.get('/current')
def current_report(db: Session = Depends(connection), actor: User = Depends(current)):
    item = db.scalar(select(Publication).order_by(Publication.dataset_id.desc(), Publication.run_id.desc()).limit(1))
    if item is None:
        return None
    source = db.get(Dataset, item.dataset_id)
    return {**views.publication(item), 'source': views.dataset(source) if source else None}
