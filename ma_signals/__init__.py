"""MA-Signals: pipeline de veille d'événements M&A (fusions-acquisitions).

Détecte en temps quasi-réel les annonces réglementaires et de presse signalant
une opération de rachat (offre possible, prise de participation, tender offer,
strategic review, etc.) sur plusieurs marchés (UK RNS, US SEC EDGAR, AMF France,
presse), les score, les stocke et envoie des alertes (Telegram / Slack).
"""

__version__ = "1.0.0"
