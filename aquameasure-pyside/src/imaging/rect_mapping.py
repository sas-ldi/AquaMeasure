"""Conversion d'un point de l'espace rectifie vers l'image reellement affichee.

Le probleme vecu, dit par le client : « quand on est en pause pour pointer la
bouchee sur la bbox, l'image est rectifiee, alors que quand on est en play,
l'image n'est pas rectifiee ». La vue stereo affiche en effet deux images
differentes selon l'etat : a l'arret l'image rectifiee du fournisseur d'images,
en lecture la video brute decodee par le lecteur natif. L'overlay, lui, est
peint par-dessus les deux avec des coordonnees en pixels de l'image
**rectifiee** : sur la video brute, tout tombe a cote.

La correspondance existe deja et va dans le bon sens. `cv2.initUndistortRectifyMap`
produit `map1x` / `map1y` tels que, pour chaque pixel **rectifie** `(x, y)`,
`map1x[y, x]` et `map1y[y, x]` donnent la coordonnee **source, brute** ou
`cv2.remap` est alle chercher son pixel. Convertir rectifie vers brut est donc
une simple lecture de tableau, sans aucune inversion a calculer.

Deux garde-fous imposes par le terrain :

- **Sans calibration, il n'y a pas de cartes.** L'image affichee est alors la
  meme dans les deux etats, et la conversion doit etre l'identite exacte, pas
  une approximation. C'est `RectMapping.identity()`.
- **La taille des cartes n'est pas supposee.** Elles sont calculees a la taille
  de la video ouverte ; si l'espace de travail de l'overlay a une autre taille,
  on met a l'echelle a l'entree et a la sortie plutot que de lire le tableau
  au mauvais endroit.
"""

from __future__ import annotations

import numpy as np

# Une bbox rectifiee ne devient pas un rectangle apres distorsion : ses bords
# se bombent. On echantillonne donc les quatre coins ET le milieu de chaque
# bord avant de prendre l'enveloppe, sinon un bord bombe vers l'exterieur
# sortirait de la boite tracee. Huit points suffisent : la distorsion radiale
# est lisse, et stereoRectify travaille ici avec alpha=0.
_BOX_SAMPLES = 8


class RectMapping:
    """Rectifie -> image affichee. Identite quand les deux espaces coincident."""

    __slots__ = ("_map_x", "_map_y", "_map_w", "_map_h", "_sx", "_sy")

    def __init__(self, map_x=None, map_y=None, frame_size=None):
        if map_x is None or map_y is None:
            self._map_x = None
            self._map_y = None
            self._map_w = 0
            self._map_h = 0
            self._sx = 1.0
            self._sy = 1.0
            return
        map_x = np.asarray(map_x, dtype=np.float32)
        map_y = np.asarray(map_y, dtype=np.float32)
        if map_x.ndim != 2 or map_x.shape != map_y.shape:
            raise ValueError("cartes de rectification incoherentes")
        self._map_x = map_x
        self._map_y = map_y
        self._map_h, self._map_w = map_x.shape
        frame_w, frame_h = frame_size or (self._map_w, self._map_h)
        frame_w = int(frame_w) or self._map_w
        frame_h = int(frame_h) or self._map_h
        # Facteur pixel de l'espace de travail -> pixel de la carte. Il vaut 1
        # dans le cas normal (cartes calculees a la taille de la video), et
        # c'est justement pour ne pas le supposer qu'il est calcule.
        self._sx = self._map_w / float(frame_w)
        self._sy = self._map_h / float(frame_h)

    # ── Etat ───────────────────────────────────────────────────────────

    @classmethod
    def identity(cls) -> "RectMapping":
        return cls()

    @property
    def is_identity(self) -> bool:
        return self._map_x is None

    @property
    def map_size(self) -> tuple[int, int]:
        return (self._map_w, self._map_h)

    # ── Conversion ─────────────────────────────────────────────────────

    def map_points(self, points):
        """Convertit une suite de (x, y). Renvoie la meme suite si identite.

        Vectorise : appeler cette methode une fois avec mille points coute le
        prix d'une indexation numpy, pas de mille appels Python.
        """
        if self._map_x is None or not len(points):
            return list(points)
        arr = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        out = self._sample(arr[:, 0], arr[:, 1])
        return [(float(x), float(y)) for x, y in out]

    def map_point(self, x: float, y: float) -> tuple[float, float]:
        if self._map_x is None:
            return (float(x), float(y))
        out = self._sample(
            np.asarray([float(x)]), np.asarray([float(y)])
        )
        return (float(out[0, 0]), float(out[0, 1]))

    def map_box(self, x1: float, y1: float, x2: float, y2: float):
        """Enveloppe, dans l'image affichee, d'une bbox donnee en rectifie."""
        if self._map_x is None:
            return (float(x1), float(y1), float(x2), float(y2))
        lo_x, hi_x = (x1, x2) if x1 <= x2 else (x2, x1)
        lo_y, hi_y = (y1, y2) if y1 <= y2 else (y2, y1)
        mid_x = (lo_x + hi_x) * 0.5
        mid_y = (lo_y + hi_y) * 0.5
        xs = np.asarray(
            [lo_x, mid_x, hi_x, hi_x, hi_x, mid_x, lo_x, lo_x], dtype=np.float64
        )
        ys = np.asarray(
            [lo_y, lo_y, lo_y, mid_y, hi_y, hi_y, hi_y, mid_y], dtype=np.float64
        )
        out = self._sample(xs, ys)
        return (
            float(out[:, 0].min()), float(out[:, 1].min()),
            float(out[:, 0].max()), float(out[:, 1].max()),
        )

    def map_boxes(self, boxes):
        """Version groupee de `map_box` : un seul passage numpy pour tout.

        C'est la voie utilisee au remplissage du cache d'overlay : convertir
        les dizaines de milliers d'echantillons de piste d'une video boite par
        boite aurait coute une seconde ; groupees, elles coutent quelques
        millisecondes.
        """
        if self._map_x is None or not len(boxes):
            return [tuple(float(v) for v in box) for box in boxes]
        arr = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
        lo_x = np.minimum(arr[:, 0], arr[:, 2])
        hi_x = np.maximum(arr[:, 0], arr[:, 2])
        lo_y = np.minimum(arr[:, 1], arr[:, 3])
        hi_y = np.maximum(arr[:, 1], arr[:, 3])
        mid_x = (lo_x + hi_x) * 0.5
        mid_y = (lo_y + hi_y) * 0.5
        xs = np.stack([lo_x, mid_x, hi_x, hi_x, hi_x, mid_x, lo_x, lo_x], axis=1)
        ys = np.stack([lo_y, lo_y, lo_y, mid_y, hi_y, hi_y, hi_y, mid_y], axis=1)
        out = self._sample(xs.reshape(-1), ys.reshape(-1))
        ox = out[:, 0].reshape(-1, _BOX_SAMPLES)
        oy = out[:, 1].reshape(-1, _BOX_SAMPLES)
        return [
            (float(ox[i].min()), float(oy[i].min()),
             float(ox[i].max()), float(oy[i].max()))
            for i in range(ox.shape[0])
        ]

    # ── Lecture bilineaire des cartes ──────────────────────────────────

    def _sample(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        """Interpole les cartes aux coordonnees rectifiees demandees.

        Les cartes sont echantillonnees au pixel entier ; un point rectifie
        tombe presque toujours entre quatre cases. L'interpolation bilineaire
        est ce que fait `cv2.remap` lui-meme : prendre le plus proche voisin
        aurait introduit un demi-pixel d'erreur, visible au zoom x8 de la vue.
        """
        mx = np.asarray(xs, dtype=np.float64) * self._sx
        my = np.asarray(ys, dtype=np.float64) * self._sy
        mx = np.clip(mx, 0.0, self._map_w - 1.0)
        my = np.clip(my, 0.0, self._map_h - 1.0)
        x0 = np.floor(mx).astype(np.int64)
        y0 = np.floor(my).astype(np.int64)
        x1 = np.minimum(x0 + 1, self._map_w - 1)
        y1 = np.minimum(y0 + 1, self._map_h - 1)
        tx = mx - x0
        ty = my - y0
        w00 = (1.0 - tx) * (1.0 - ty)
        w10 = tx * (1.0 - ty)
        w01 = (1.0 - tx) * ty
        w11 = tx * ty
        out = np.empty((mx.size, 2), dtype=np.float64)
        for axis, table in enumerate((self._map_x, self._map_y)):
            out[:, axis] = (
                table[y0, x0] * w00 + table[y0, x1] * w10
                + table[y1, x0] * w01 + table[y1, x1] * w11
            )
        # Les cartes rendent des pixels de leur propre grille : on revient
        # dans l'espace de travail de l'overlay par le meme facteur.
        out[:, 0] /= self._sx
        out[:, 1] /= self._sy
        return out
