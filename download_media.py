#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Télécharge les photos des lieux (depuis Wikimedia Commons) dans photos/<id>/
et génère photos-data.js, lu par index.html.

À lancer UNE FOIS, sur une machine connectée à Internet :

    python3 download_media.py

Puis committez les dossiers photos/ et le fichier photos-data.js dans votre
dépôt GitHub. Les photos s'afficheront alors via le bouton « Voir les photos »,
en ligne (GitHub Pages) comme hors ligne.

Aucune dépendance : uniquement la bibliothèque standard de Python 3.
Sources : Wikimedia Commons (images sous licence libre — crédit/licence repris
automatiquement et affichés au clic sur chaque photo).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# --- Réglages -------------------------------------------------------------
MAX_PAR_LIEU = 6          # nb max de photos téléchargées par lieu
LARGEUR = 1024            # largeur demandée (px)
PAUSE = 1.0               # pause entre requêtes (politesse envers Wikimedia)
UA = "GeorgieTripMap/1.0 (carte de voyage perso, usage non commercial)"
EXT_OK = (".jpg", ".jpeg", ".png", ".webp")
CWEBP = shutil.which("cwebp")   # si présent : enregistre en WebP (plus léger)
WEBP_Q = 80                     # qualité WebP

ICI = os.path.dirname(os.path.abspath(__file__))
DOSSIER_PHOTOS = os.path.join(ICI, "photos")
DOSSIER_MENUS = os.path.join(ICI, "menus")
SORTIE = os.path.join(ICI, "photos-data.js")

# --- Articles Wikipédia par lieu (lang:Titre) -----------------------------
# (doit rester synchrone avec WIKITITLE dans index.html)
WIKITITLE = {
    # Tbilissi
    "tb-sameba": "en:Holy Trinity Cathedral of Tbilisi",
    "tb-narikala": "en:Narikala",
    "tb-kartlis": "en:Kartlis Deda",
    "tb-chronicle": "en:Chronicle of Georgia",
    "tb-peace": "en:Bridge of Peace (Tbilisi)",
    "tb-gabriadze": "en:Gabriadze Theater",
    "tb-mtatsminda": "en:Mtatsminda Park",
    "tb-abano": "en:Abanotubani",
    "tb-juma": "en:Tbilisi Mosque",
    "tb-armenian": "en:Saint George's Cathedral, Tbilisi",
    "tb-anchiskhati": "en:Anchiskhati Basilica",
    "tb-didgori": "en:Battle of Didgori",
    "tb-fabrika": "en:Fabrika (Tbilisi)",
    "tb-q-sololaki": "en:Sololaki",
    # Mtskheta
    "mt-svet": "en:Svetitskhoveli Cathedral",
    "mt-jvari": "en:Jvari (monastery)",
    # Ananuri / route
    "an-fort": "en:Ananuri",
    "an-jinvali": "en:Zhinvali Reservoir",
    # Kazbegi
    "kz-gergeti": "en:Gergeti Trinity Church",
    "kz-dariali": "en:Dariali Gorge",
    "kz-truso": "en:Truso Valley",
    # Borjomi
    "bo-np": "en:Borjomi-Kharagauli National Park",
    "bo-park": "en:Borjomi",
    # Koutaïssi
    "ku-bagrati": "en:Bagrati Cathedral",
    "ku-gelati": "en:Gelati Monastery",
    "ku-prometheus": "en:Prometheus Cave",
    "ku-okatse": "en:Okatse Canyon",
    "ku-martvili": "en:Martvili Canyon",
    "ku-sataplia": "en:Sataplia Nature Reserve",
    "ku-botanic": "en:Kutaisi Botanical Garden",
    # Batumi
    "ba-alinino": "en:Ali and Nino (sculpture)",
    "ba-botanic": "en:Batumi Botanical Garden",
    "ba-boulevard": "en:Batumi Boulevard",
}


def _request(url, headers, timeout, binary):
    """Requête HTTP avec retries + backoff sur 429/503 (limites Wikimedia)."""
    req = urllib.request.Request(url, headers=headers)
    delay = 2.0
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return raw if binary else json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 5:
                ra = (e.headers.get("Retry-After") or "").strip()
                wait = float(ra) if ra.isdigit() else delay
                print("    … HTTP %d — nouvelle tentative dans %.0fs" % (e.code, wait))
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            raise


def http_json(url):
    return _request(url, {"User-Agent": UA, "Accept": "application/json"}, 30, False)


def http_bytes(url):
    return _request(url, {"User-Agent": UA}, 60, True)


def _resolve_title(lang, title):
    """Résout le titre exact (suit redirections + normalisation). None si absent."""
    url = ("https://%s.wikipedia.org/w/api.php?action=query&format=json&redirects=1&titles=%s"
           % (lang, urllib.parse.quote(title)))
    try:
        pages = http_json(url).get("query", {}).get("pages", {})
        page = next(iter(pages.values()))
        return None if "missing" in page else page.get("title")
    except Exception:
        return None


def _rest_media(lang, title):
    t = urllib.parse.quote(title.replace(" ", "_"), safe="")
    data = http_json("https://%s.wikipedia.org/api/rest_v1/page/media-list/%s" % (lang, t))
    out = []
    for it in data.get("items", []):
        if it.get("type") == "image":
            name = it.get("title", "").split(":", 1)[-1]
            if name.lower().endswith(EXT_OK):
                out.append(name)
    return out


def _api_images(lang, title):
    url = ("https://%s.wikipedia.org/w/api.php?action=query&format=json&prop=images"
           "&redirects=1&imlimit=80&titles=%s" % (lang, urllib.parse.quote(title)))
    pages = http_json(url).get("query", {}).get("pages", {})
    page = next(iter(pages.values()))
    out = []
    for im in page.get("images", []):
        name = im.get("title", "").split(":", 1)[-1]
        if name.lower().endswith(EXT_OK) and "logo" not in name.lower() and "icon" not in name.lower():
            out.append(name)
    return out


def media_list(lang, title):
    """Images d'un article : titre résolu -> REST media-list (soignée) -> repli API images."""
    canon = _resolve_title(lang, title)
    if canon is None:
        print("    (article Wikipédia introuvable)")
        return []
    try:
        imgs = _rest_media(lang, canon)
        if imgs:
            return imgs
    except Exception:
        pass
    try:
        return _api_images(lang, canon)
    except Exception as e:
        print("    ! images KO (%s)" % e)
        return []


def licence_credit(filename):
    """Récupère licence + auteur depuis Commons (best effort)."""
    try:
        url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
               "&prop=imageinfo&iiprop=extmetadata&titles=" +
               urllib.parse.quote("File:" + filename, safe=""))
        data = http_json(url)
        pages = data.get("query", {}).get("pages", {})
        page = next(iter(pages.values()))
        meta = page["imageinfo"][0]["extmetadata"]

        def clean(v):
            return re.sub("<[^>]+>", "", v or "").strip()
        lic = clean(meta.get("LicenseShortName", {}).get("value", "")) or "Wikimedia Commons"
        art = clean(meta.get("Artist", {}).get("value", ""))
        return lic, art
    except Exception:
        return "Wikimedia Commons", ""


def commons_page(filename):
    return "https://commons.wikimedia.org/wiki/File:" + urllib.parse.quote(filename.replace(" ", "_"), safe="")


def filepath_url(filename, width):
    return ("https://commons.wikimedia.org/wiki/Special:FilePath/" +
            urllib.parse.quote(filename.replace(" ", "_"), safe="") +
            "?width=%d" % width)


def scan_menus():
    """menus/<id>.pdf -> PDF ; menus/<id>/*.jpg|webp|png -> liste d'images (menu photographié sur place)."""
    pdfs, imgs = {}, {}
    if os.path.isdir(DOSSIER_MENUS):
        for f in sorted(os.listdir(DOSSIER_MENUS)):
            full = os.path.join(DOSSIER_MENUS, f)
            if f.lower().endswith(".pdf"):
                pdfs[os.path.splitext(f)[0]] = "menus/" + f
            elif os.path.isdir(full):
                pics = sorted(x for x in os.listdir(full) if x.lower().endswith(EXT_OK))
                if pics:
                    imgs[f] = ["menus/%s/%s" % (f, x) for x in pics]
    return pdfs, imgs


def main():
    os.makedirs(DOSSIER_PHOTOS, exist_ok=True)
    photos = {}
    total = 0
    print("Téléchargement des photos depuis Wikimedia Commons…\n")
    for pid, wt in WIKITITLE.items():
        lang, title = wt.split(":", 1)
        print("• %-16s %s" % (pid, wt))
        names = media_list(lang, title)
        if not names:
            print("    (aucune image trouvée — le bouton proposera un lien)")
            time.sleep(PAUSE)
            continue
        dest = os.path.join(DOSSIER_PHOTOS, pid)
        os.makedirs(dest, exist_ok=True)
        liste = []
        n = 0
        for name in names:
            if n >= MAX_PAR_LIEU:
                break
            ext = os.path.splitext(name)[1].lower()
            if ext == ".jpeg":
                ext = ".jpg"
            try:
                data = http_bytes(filepath_url(name, LARGEUR))
                if len(data) < 3000:  # vignette cassée / trop petite
                    continue
                if CWEBP:
                    fichier = "%d.webp" % (n + 1)
                    chemin = os.path.join(dest, fichier)
                    tmp = chemin + ext
                    with open(tmp, "wb") as fh:
                        fh.write(data)
                    try:
                        subprocess.run([CWEBP, "-quiet", "-q", str(WEBP_Q), tmp, "-o", chemin],
                                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    finally:
                        os.remove(tmp)
                else:
                    fichier = "%d%s" % (n + 1, ext)
                    chemin = os.path.join(dest, fichier)
                    with open(chemin, "wb") as fh:
                        fh.write(data)
            except Exception as e:
                print("    ! %s : %s" % (name, e))
                continue
            lic, art = licence_credit(name)
            liste.append({
                "file": "photos/%s/%s" % (pid, fichier),
                "credit": (art + " — " if art else "") + name,
                "license": lic,
                "source": commons_page(name),
            })
            n += 1
            total += 1
            time.sleep(PAUSE)
        if liste:
            photos[pid] = liste
            print("    %d photo(s) -> %s/" % (len(liste), os.path.relpath(dest, ICI)))
        time.sleep(PAUSE)

    menus, menuimg = scan_menus()

    with open(SORTIE, "w", encoding="utf-8") as fh:
        fh.write("/* Généré par download_media.py — photos téléchargées depuis Wikimedia Commons. */\n")
        fh.write("window.PHOTOS = " + json.dumps(photos, ensure_ascii=False, indent=2) + ";\n")
        fh.write("window.MENUS = " + json.dumps(menus, ensure_ascii=False, indent=2) + ";\n")
        fh.write("window.MENUIMG = " + json.dumps(menuimg, ensure_ascii=False, indent=2) + ";\n")

    print("\nTerminé : %d photos pour %d lieux." % (total, len(photos)))
    if menus:
        print("Menus PDF détectés : %s" % ", ".join(menus))
    print("Fichier écrit : %s" % os.path.relpath(SORTIE, ICI))
    print("\nPensez à committer  photos/  ,  menus/  et  photos-data.js  dans votre dépôt.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)
