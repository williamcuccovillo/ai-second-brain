"""
Riordino automatico della Inbox del Second Brain di Will (vault Obsidian su GitHub privato).

Una volta al giorno (alle 21, ora italiana) il server scarica le novità del vault in
`~/second-brain/` e conta le note nella Inbox. Solo se ce ne sono sveglia Carlo con un avviso: lui
le mette in ordine secondo `Come funziona.md` del vault, salva su GitHub e scrive due righe a Will.
Se la Inbox è vuota non succede niente e non si consuma l'abbonamento. Se lo scaricamento non
riesce, Carlo riceve l'errore e lo spiega a Will. Se il server era spento all'ora prevista, il
controllo parte al primo giro utile. Lo stato sta in `dati/second-brain.json`.

Ogni domenica alle 18 c'è anche il ripasso settimanale: se la Knowledge Base ha schede, Carlo
riceve un avviso e manda a Will cinque domande prese da lì, più il riepilogo della settimana.
Dal 01/11/2026 il ripasso è spento su richiesta di Will (REVIEW_ENABLED): si riaccende quando lo chiede.
"""

import asyncio
import json
import logging
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from . import chat_texts
from .config import DATA_DIR
from .usage import LOCAL_TIME

log = logging.getLogger(__name__)

VAULT = Path.home() / "second-brain"
STATE_FILE = DATA_DIR / "second-brain.json"
TIDY_HOURS = (21,)
REVIEW_WEEKDAY, REVIEW_HOUR = 6, 18  # domenica alle 18
REVIEW_ENABLED = False  # spento da Will il 01/11/2026, da riaccendere su sua richiesta
CHECK_SECONDS = 10 * 60
PULL_TIMEOUT_SECONDS = 120


class SecondBrainInbox:
    def __init__(self, project_bot, vault: Path = VAULT, state_file: Path = STATE_FILE,
                 pull=None):
        self.bot, self.vault, self.state_file = project_bot, vault, state_file
        self.pull = pull or pull_vault

    async def run_forever(self) -> None:
        while True:
            try:
                await self.check(datetime.now(LOCAL_TIME))
            except Exception:
                log.exception("Controllo della Inbox del Second Brain non riuscito")
            await asyncio.sleep(CHECK_SECONDS)

    async def check(self, now: datetime) -> None:
        state = self._load()
        tidy, review = last_slot(now).isoformat(), last_review_slot(now).isoformat()
        tidy_due = state.get("ultimo_turno", "") < tidy
        review_due = state.get("ultimo_ripasso", "") < review
        if not (tidy_due or review_due):
            return
        self.state_file.write_text(json.dumps({"ultimo_turno": tidy, "ultimo_ripasso": review}))
        if not self.vault.is_dir():
            return
        notice = self.bot.worker(self.bot.assistant.authorized_user).add_server_notice
        error = await asyncio.to_thread(self.pull, self.vault)
        if error:
            log.warning("Second Brain: scaricamento non riuscito (%s)", error)
            notice(chat_texts.second_brain_pull_failed(error))
            return
        notes = inbox_notes(self.vault) if tidy_due else []
        log.info("Second Brain: %d note nella Inbox", len(notes))
        if notes:
            notice(chat_texts.second_brain_inbox(self.vault, notes))
        if REVIEW_ENABLED and review_due and knowledge_cards(self.vault):
            log.info("Second Brain: ripasso settimanale")
            notice(chat_texts.second_brain_review(self.vault))

    def _load(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return {}


def last_slot(now: datetime) -> datetime:
    """L'ultimo orario di riordino già passato rispetto a `now`."""
    for day_back in (0, 1):
        day = now - timedelta(days=day_back)
        for hour in sorted(TIDY_HOURS, reverse=True):
            slot = day.replace(hour=hour, minute=0, second=0, microsecond=0)
            if slot <= now:
                return slot
    raise AssertionError("TIDY_HOURS vuoto")


def last_review_slot(now: datetime) -> datetime:
    """L'ultima domenica alle 18 già passata rispetto a `now`."""
    days_back = (now.weekday() - REVIEW_WEEKDAY) % 7
    slot = (now - timedelta(days=days_back)).replace(hour=REVIEW_HOUR, minute=0, second=0,
                                                     microsecond=0)
    return slot if slot <= now else slot - timedelta(days=7)


def knowledge_cards(vault: Path) -> list[str]:
    folder = vault / "Knowledge Base"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.glob("*.md") if p.name != "Ripassi.md")


def inbox_notes(vault: Path) -> list[str]:
    inbox = vault / "Inbox"
    if not inbox.is_dir():
        return []
    return sorted(p.name for p in inbox.iterdir() if p.is_file() and not p.name.startswith("."))


def pull_vault(vault: Path) -> str:
    """Scarica le novità da GitHub; restituisce il messaggio d'errore, o "" se è andata."""
    try:
        done = subprocess.run(["git", "-C", str(vault), "pull", "-q", "--no-rebase"],
                              capture_output=True, text=True, timeout=PULL_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return f"nessuna risposta da GitHub entro {PULL_TIMEOUT_SECONDS} secondi"
    return "" if done.returncode == 0 else (done.stderr.strip() or f"codice {done.returncode}")
