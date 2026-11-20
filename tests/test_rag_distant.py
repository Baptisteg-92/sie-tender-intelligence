"""F6 branché sur le service RAG (rag_sie) : la trame du mémoire reçoit des passages sourcés."""
import socket
import threading
import time

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from rag_sie.config import ConfigRAG  # noqa: E402
from rag_sie.demo import generer  # noqa: E402
from rag_sie.encodeurs import EncodeurHachage  # noqa: E402
from rag_sie.moteur import Moteur  # noqa: E402
from rag_sie.service import creer_app  # noqa: E402
from sie_tender.f6_archives import construire_trame, references_similaires  # noqa: E402
from sie_tender.models import Critere  # noqa: E402
from sie_tender.rag_distant import BibliothequeRAG  # noqa: E402


@pytest.fixture
def service(tmp_path):
    archives = generer(tmp_path / "archives")
    m = Moteur(ConfigRAG(donnees=tmp_path / "d", archives=str(archives), droits="fichier", jeton="j"), encodeur=EncodeurHachage())
    m.indexeur.synchroniser()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    serveur = uvicorn.Server(uvicorn.Config(creer_app(m, planifier=False), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=serveur.run, daemon=True).start()
    for _ in range(100):
        if serveur.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    serveur.should_exit = True


def test_trame_avec_le_service_rag(service):
    b = BibliothequeRAG(service, "j", "SIE\\jdupont", "SIE\\BE")
    criteres = [Critere(intitule="Valeur technique", ponderation=60,
                        sous_criteres=[Critere(intitule="Gestion des déchets de chantier", ponderation=20)])]
    trame = construire_trame(criteres, b)
    dechets = next(s for s in trame if "déchets" in s.titre)
    assert dechets.passages_proposes and "tri" in dechets.passages_proposes[0].texte
    assert dechets.passages_proposes[0].source.fichier.endswith(".docx")
    refs = references_similaires(b, "remplacement SSI en site occupé avec des résidents")
    assert refs and refs[0].annee


def test_service_absent_sans_erreur():
    assert BibliothequeRAG("http://127.0.0.1:9", delai=0.5).rechercher("déchets") == []
