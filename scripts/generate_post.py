#!/usr/bin/env python3
"""Génère et publie l'article du jour du blog satellite.

Étapes :
  1. Vérifie que le dernier article date d'au moins BLOG_INTERVAL_DAYS jours (2 par défaut).
  2. Prend le prochain sujet non publié dans scripts/topics.json.
  3. Demande l'article à l'API Anthropic (clé lue dans ANTHROPIC_API_KEY).
  4. Valide le résultat (longueur, format, liens, avertissement santé...).
  5. Écrit _posts/AAAA-MM-JJ-slug.md et marque le sujet comme publié.

Si la validation échoue (après MAX_ATTEMPTS essais), rien n'est écrit et le
script se termine avec le code 1 : le workflow échoue proprement.

Options :
  --dry-run          n'écrit rien dans _posts/ ni topics.json ; l'aperçu va dans _preview/
  --from-file PATH   utilise un texte brut déjà rédigé au lieu d'appeler l'API (tests hors ligne)
  --not-before HOUR  ne publie pas avant cette heure (heure de Paris) ; utilisé par le cron
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TOPICS_FILE = ROOT / "scripts" / "topics.json"
POSTS_DIR = ROOT / "_posts"
PREVIEW_DIR = ROOT / "_preview"
TZ = ZoneInfo("Europe/Paris")

MODEL = os.environ.get("BLOG_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("BLOG_EFFORT", "medium")
MAX_ATTEMPTS = 2
# Nombre de jours minimum entre deux articles (2 = un article tous les 2 jours)
INTERVAL_DAYS = int(os.environ.get("BLOG_INTERVAL_DAYS", "2"))

MIN_WORDS, MAX_WORDS = 600, 900
MAX_DESCRIPTION = 155
SITE_URL = "https://protokole.fr"

FORBIDDEN = [  # promesses médicales à proscrire
    r"\bgu[ée]ri[rst]?\b", r"\bgu[ée]rison\b", r"\bmiracle\b", r"\bsoigner\b",
    r"\bsoigne\b", r"\bd[ée]tox(?:ifi\w*)?\b", r"\bbr[uû]le[- ]graisses?\b",
]

SYSTEM_PROMPT = f"""Tu es rédacteur web pour un blog francophone sur l'alimentation saine au quotidien : idées de repas, meal prep, courses équilibrées et pas chères.
Public : francophones de 20 à 45 ans qui veulent mieux manger sans se compliquer la vie.

RÈGLES DE RÉDACTION (toutes obligatoires)
- Longueur du corps : entre 650 et 850 mots (jamais moins de 600, jamais plus de 900).
- Titre accrocheur qui contient le mot-clé principal mot pour mot.
- Intro courte (2 à 4 phrases), puis au moins 3 sous-titres H2 (## ...). Pas de titre H1 dans le corps.
- Utilise des listes à puces ou numérotées et des conseils concrets : quantités, temps, étapes, prix indicatifs, astuces.
- Contenu réellement utile et précis. Aucun remplissage, aucune généralité creuse.
- Ton simple, direct, sympathique. Tutoiement interdit : vouvoiement.
- Aucune promesse médicale ni conseil thérapeutique : pas de « guérir », « soigner », « détox », « miracle », « brûle-graisses ». Reste sur de l'information générale.
- Mentionne protokole.fr exactement DEUX fois, toujours sous forme de lien Markdown vers {SITE_URL} :
  1) une fois dans le corps, là où c'est naturellement utile (organiser ses courses, planifier ses menus, etc.) ;
  2) une fois dans la conclusion (dernière section).
  Ton naturel, jamais publicitaire ni insistant. Aucune autre mention de protokole.
- Termine par une phrase invitant à consulter un professionnel de santé (médecin, diététicien·ne) pour un cas personnel.

FORMAT DE SORTIE (strict, rien avant, rien après) :
TITLE: <titre>
DESCRIPTION: <méta-description, 155 caractères maximum, avec le mot-clé>
TAGS: <3 à 5 tags en minuscules séparés par des virgules>
---
<corps de l'article en Markdown>"""


def user_prompt(topic: dict) -> str:
    return (
        f"Rédige l'article suivant.\n"
        f"- Sujet / titre de travail : {topic['title']}\n"
        f"- Mot-clé principal : {topic['keyword']}\n"
        f"- Intention de recherche : {topic['search_intent']}\n"
        f"- Type d'article : {topic['type']}\n"
        f"{TYPE_HINTS.get(topic['type'], '')}"
    )


TYPE_HINTS = {
    "recette": "- Pour une recette : portions, temps de préparation et de cuisson, liste d'ingrédients avec quantités, étapes numérotées, variantes et conservation.\n",
    "guide": "- Pour un guide : méthode pas à pas, exemples chiffrés, erreurs à éviter.\n",
    "liste": "- Pour une liste : éléments numérotés, chacun avec un détail concret (quantité, temps, prix ou astuce).\n",
    "comparatif": "- Pour un comparatif : critères clairs (prix, nutrition, praticité), un tableau Markdown récapitulatif et une conclusion nuancée selon les besoins.\n",
}


# --------------------------------------------------------------------------- utils

def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def norm_words(text: str) -> list[str]:
    text = strip_accents(text.lower()).replace("œ", "oe").replace("’", "'")
    return re.findall(r"[a-z0-9]+", text)


def slugify(text: str, max_len: int = 70) -> str:
    slug = "-".join(norm_words(text))
    return slug[:max_len].rstrip("-") or "article"


def count_words(markdown: str) -> int:
    text = re.sub(r"\]\([^)]*\)", "]", markdown)  # retire les URL des liens
    return len(re.findall(r"\w+(?:['’-]\w+)*", text))


def latest_post_date() -> date | None:
    dates = []
    for p in POSTS_DIR.glob("*.md"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-", p.name)
        if m:
            dates.append(date.fromisoformat(m.group(1)))
    return max(dates) if dates else None


def load_topics() -> list[dict]:
    return json.loads(TOPICS_FILE.read_text(encoding="utf-8"))


def save_topics(topics: list[dict]) -> None:
    TOPICS_FILE.write_text(json.dumps(topics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def yaml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ------------------------------------------------------------------ génération API

def call_api(topic: dict) -> str:
    import anthropic  # importé ici pour que --from-file fonctionne sans le SDK

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ERREUR : la variable d'environnement ANTHROPIC_API_KEY n'est pas définie.")

    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt(topic)}],
        # Si le modèle refuse, l'API relance automatiquement la requête sur un modèle de repli.
        betas=["server-side-fallback-2026-07-01"],
        extra_body={"fallbacks": "default", "output_config": {"effort": EFFORT}},
    ) as stream:
        message = stream.get_final_message()

    if message.stop_reason == "refusal":
        raise ValueError("le modèle a refusé la requête")
    if message.stop_reason == "max_tokens":
        raise ValueError("réponse tronquée (max_tokens atteint)")
    text = "".join(b.text for b in message.content if b.type == "text").strip()
    u = message.usage
    print(f"  API : {MODEL}, {u.input_tokens} tokens en entrée, {u.output_tokens} en sortie")
    return text


# -------------------------------------------------------------------- validation

def parse_output(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:markdown|md)?\s*\n(.*)\n```$", r"\1", raw, flags=re.S)
    head, sep, body = raw.partition("\n---\n")
    if not sep:
        raise ValueError("séparateur '---' introuvable entre l'en-tête et le corps")
    fields = {}
    for line in head.splitlines():
        m = re.match(r"^(TITLE|DESCRIPTION|TAGS)\s*:\s*(.+)$", line.strip())
        if m:
            fields[m.group(1)] = m.group(2).strip().strip('"')
    missing = {"TITLE", "DESCRIPTION", "TAGS"} - fields.keys()
    if missing:
        raise ValueError(f"champs manquants dans l'en-tête : {', '.join(sorted(missing))}")
    tags = [t.strip().lower() for t in fields["TAGS"].split(",") if t.strip()]
    return {"title": fields["TITLE"], "description": fields["DESCRIPTION"], "tags": tags, "body": body.strip()}


def validate(article: dict, topic: dict) -> list[str]:
    errors = []
    title, desc, tags, body = article["title"], article["description"], article["tags"], article["body"]

    title_words = set(norm_words(title))
    missing_kw = [w for w in norm_words(topic["keyword"]) if len(w) > 2 and w not in title_words]
    if missing_kw:
        errors.append(f"le titre ne contient pas le mot-clé « {topic['keyword']} » (manque : {', '.join(missing_kw)})")
    if not (20 <= len(title) <= 110):
        errors.append(f"titre de longueur anormale ({len(title)} caractères)")

    if not (50 <= len(desc) <= MAX_DESCRIPTION):
        errors.append(f"description de {len(desc)} caractères (attendu : 50 à {MAX_DESCRIPTION})")

    if not (2 <= len(tags) <= 6):
        errors.append(f"{len(tags)} tags (attendu : 2 à 6)")

    words = count_words(body)
    if not (MIN_WORDS <= words <= MAX_WORDS):
        errors.append(f"{words} mots (attendu : {MIN_WORDS} à {MAX_WORDS})")

    if re.search(r"^#\s", body, flags=re.M):
        errors.append("le corps contient un titre H1")
    h2 = [m.start() for m in re.finditer(r"^##\s+\S", body, flags=re.M)]
    if len(h2) < 3:
        errors.append(f"{len(h2)} sous-titres H2 (minimum 3)")
    if not re.search(r"^\s*(?:[-*]|\d+\.)\s+\S", body, flags=re.M):
        errors.append("aucune liste dans l'article")

    links = [m.start() for m in re.finditer(r"\]\(https?://(?:www\.)?protokole\.fr[^)]*\)", body)]
    mentions = len(re.findall(r"protokole", body, flags=re.I))
    if len(links) != 2:
        errors.append(f"{len(links)} lien(s) Markdown vers protokole.fr (attendu : exactement 2)")
    elif h2 and not (links[0] < h2[-1] < links[1]):
        errors.append("il faut un lien protokole.fr dans le corps et un dans la conclusion (dernière section)")
    # chaque lien compte au plus 2 occurrences (texte + URL)
    if mentions > 2 * max(len(links), 1):
        errors.append("protokole est mentionné en dehors des deux liens prévus")

    tail = strip_accents(body[-600:].lower())
    if not re.search(r"professionnel de (la )?sante|medecin|dieteticien|nutritionniste", tail):
        errors.append("pas d'invitation finale à consulter un professionnel de santé")

    for pattern in FORBIDDEN:
        m = re.search(pattern, strip_accents(body.lower()))
        if m:
            errors.append(f"terme à proscrire détecté : « {m.group(0)} »")

    if "```" in body:
        errors.append("bloc de code inattendu dans l'article")
    return errors


# -------------------------------------------------------------------- écriture

def render_post(article: dict, topic: dict, now: datetime) -> str:
    tags = ", ".join(yaml_str(t) for t in article["tags"])
    return (
        "---\n"
        "layout: post\n"
        f"type: {topic['type']}\n"
        f"title: {yaml_str(article['title'])}\n"
        f"date: {now.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
        f"description: {yaml_str(article['description'])}\n"
        f"tags: [{tags}]\n"
        "---\n\n"
        f"{article['body']}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--from-file", type=Path)
    parser.add_argument("--not-before", type=int, default=None, metavar="HOUR")
    args = parser.parse_args()

    now = datetime.now(TZ).replace(microsecond=0)
    today = now.strftime("%Y-%m-%d")

    if args.not_before is not None and now.hour < args.not_before:
        print(f"Il est {now:%H:%M} à Paris, avant {args.not_before}h : rien à faire pour l'instant.")
        return 0
    last = latest_post_date()
    if not args.dry_run and last and (now.date() - last).days < INTERVAL_DAYS:
        next_day = last + timedelta(days=INTERVAL_DAYS)
        print(f"Dernier article le {last:%d/%m/%Y} ; prochain prévu le {next_day:%d/%m/%Y} : rien à faire.")
        return 0

    topics = load_topics()
    topic = next((t for t in topics if not t.get("published")), None)
    if topic is None:
        print("ERREUR : tous les sujets de topics.json ont été publiés. Ajoutez-en de nouveaux.")
        return 1
    print(f"Sujet n°{topic['id']} : {topic['title']} [{topic['type']}]")

    article, errors = None, []
    attempts = 1 if args.from_file else MAX_ATTEMPTS
    for attempt in range(1, attempts + 1):
        try:
            raw = args.from_file.read_text(encoding="utf-8") if args.from_file else call_api(topic)
            article = parse_output(raw)
            errors = validate(article, topic)
        except Exception as exc:  # erreur API ou format illisible
            errors = [str(exc)]
        if not errors:
            break
        print(f"  Essai {attempt}/{attempts} rejeté :")
        for e in errors:
            print(f"    - {e}")

    if errors:
        print("ÉCHEC : l'article ne respecte pas les règles. Rien n'est publié.")
        return 1

    content = render_post(article, topic, now)
    filename = f"{today}-{slugify(article['title'])}.md"
    print(f"  OK : {count_words(article['body'])} mots, {len(article['description'])} car. de description")

    if args.dry_run:
        PREVIEW_DIR.mkdir(exist_ok=True)
        out = PREVIEW_DIR / filename
        out.write_text(content, encoding="utf-8")
        print(f"[dry-run] Aperçu écrit dans {out.relative_to(ROOT)} (topics.json inchangé)")
        return 0

    (POSTS_DIR / filename).write_text(content, encoding="utf-8")
    topic["published"] = today
    topic["post"] = filename
    save_topics(topics)
    print(f"Publié : _posts/{filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
