#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Génère l'audio géorgien des pages phrases.html et glossaire.html -> audio/ka/<n>
et écrit audio-ka.js (window.KAUDIO : texte géorgien -> fichier audio).

Voix, par ordre de préférence :
  1. edge-tts (voix neuronale Microsoft « ka-GE-EkaNeural », naturelle, gratuite,
     sans clé, mais en ligne) :   pip install edge-tts
  2. eSpeak NG (repli hors-ligne, synthétique/robotique) : brew install espeak-ng

    python3 gen_audio.py

Relancer après avoir ajouté/édité des phrases.
"""
import html
import json
import os
import re
import shutil
import subprocess

ICI = os.path.dirname(os.path.abspath(__file__))
PAGES = ["phrases.html", "glossaire.html"]
DEST = os.path.join(ICI, "audio", "ka")
EDGE_VOICE = "ka-GE-EkaNeural"   # voix féminine géorgienne (essayer aussi ka-GE-GiorgiNeural)
EDGE_RATE = "-15%"               # un peu plus lent que la normale (clarté)


def collect():
    seen, texts = set(), []
    for p in PAGES:
        s = open(os.path.join(ICI, p), encoding="utf-8").read()
        for m in re.findall(r'data-ka="([^"]+)"', s):
            t = html.unescape(m).strip()
            if t and t not in seen:
                seen.add(t)
                texts.append(t)
    return texts


def synth_edge(text, out):
    import asyncio
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, EDGE_VOICE, rate=EDGE_RATE).save(out))


def synth_espeak(text, out):
    wav = "/tmp/_ka_tts.wav"
    with open(wav, "wb") as fh:
        subprocess.run(["espeak-ng", "-v", "ka", "-s", "140", text, "--stdout"], stdout=fh, check=True)
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", "-b", "64000", wav, out],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    try:
        import edge_tts  # noqa: F401
        engine, ext, synth = "edge-tts (voix neuronale)", "mp3", synth_edge
    except Exception:
        engine, ext, synth = "eSpeak NG (repli)", "m4a", synth_espeak

    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    os.makedirs(DEST)

    texts = collect()
    mapping = {}
    for i, t in enumerate(texts, 1):
        rel = "audio/ka/%d.%s" % (i, ext)
        synth(t, os.path.join(DEST, "%d.%s" % (i, ext)))
        mapping[t] = rel

    with open(os.path.join(ICI, "audio-ka.js"), "w", encoding="utf-8") as fh:
        fh.write("/* Audio géorgien — %s. Régénérer : python3 gen_audio.py */\n" % engine)
        fh.write("window.KAUDIO = " + json.dumps(mapping, ensure_ascii=False) + ";\n")

    kb = sum(os.path.getsize(os.path.join(DEST, f)) for f in os.listdir(DEST)) / 1024
    print("Généré : %d clips via %s (%.0f Ko) + audio-ka.js" % (len(mapping), engine, kb))


if __name__ == "__main__":
    main()
