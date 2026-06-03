"""Globe nation highlighter.

# XXX: This module renders orthographic-projection globe images with a
# highlighted country, styled after Wikipedia's country location maps.
# It uses Natural Earth 110m shapefiles (auto-downloaded on first use)
# and Matplotlib + Cartopy for rendering.
#
# Key public API:
#   get_nation_globe_image(nation_name)  -> bytes  (PNG)
#   get_random_nation_name(continent)    -> str | None
#   warm_up()                            -> None   (call at bot startup)
#
# Performance notes:
#   First call is slow (~2-3 s) because it downloads/loads the shapefile and
#   pre-computes shading arrays.  Every subsequent call is fast (~0.3-0.6 s)
#   because all of the following are cached at module level:
#
#     _WORLD_GDF          — GeoDataFrame loaded once, never re-read
#     _WORLD_GEOMS_4326   — geometries already projected to EPSG:4326
#     _DARK_RGBA          — Lambertian + limb-darkening RGBA array  (float32)
#     _SPEC_RGBA          — Phong specular RGBA array               (float32)
#     _NATION_CENTERS     — dict[lower_name -> (lon, lat)] pre-computed
#                           for every country so to_crs() / representative_point()
#                           are never called inside the hot path
#
#   Cartopy cfeature (which triggers its own network downloads) is replaced
#   with geometries drawn directly from the already-loaded shapefile.
#
# Shading pipeline:
#   - Lambertian diffuse  (light from top-left)
#   - Limb darkening      (edge of sphere gets darker)
#   - Phong specular      (small glossy highlight)
#   All three overlays are composited in axes-fraction space so they
#   clip perfectly to the globe circle with no bleed onto the white
#   background.
#
# Auto-centering:
#   The globe is rotated so the target country's representative point
#   sits at the centre of the orthographic projection.
"""

import io
import logging
import random
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

import cartopy.crs as ccrs
import geopandas as gpd
import matplotlib
import matplotlib.pyplot as plt
import numpy as np

matplotlib.use("Agg")  # non-interactive backend — avoids GTK/display crashes

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shapefile configuration
# ---------------------------------------------------------------------------

ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
_SHAPEFILE_DIR = ASSETS_DIR / "ne_110m_admin_0_countries"
_SHAPEFILE_PATH = _SHAPEFILE_DIR / "ne_110m_admin_0_countries.shp"
_SHAPEFILE_URL = (
    "https://naciscdn.org/naturalearth/110m/cultural/"
    "ne_110m_admin_0_countries.zip"
)

# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------

_HIGHLIGHT_COLOR = "#2d5a1b"  # dark green (Wikipedia style)
_HIGHLIGHT_EDGE = "#1a3a0f"
_LAND_COLOR = "#d0d0d0"
_OCEAN_COLOR = "#a8c8e8"
_BORDER_COLOR = "#999999"
_GRID_COLOR = "#888888"

# Shading intensities — tweak these to taste
_LIMB_STRENGTH = 0.25  # edge darkening  (0 = none, 1 = full black)
_SHADOW_STRENGTH = 0.22  # directional shadow
_DARK_CAP = 0.38  # hard ceiling for combined darkness
_SPEC_STRENGTH = 0.45  # specular highlight brightness
_SPEC_SHININESS = 22  # Phong exponent (higher = tighter spot)
_LIGHT = (-0.4, 0.6, 0.7)  # light direction (normalised in code)

# Shading grid resolution — 400 is indistinguishable from 800 at normal sizes
_SHADE_SIZE = 400

# PNG output DPI — 150 is plenty for Telegram; raise to 200 for higher quality
_OUTPUT_DPI = 150

# ---------------------------------------------------------------------------
# Continent name normalisation map
# ---------------------------------------------------------------------------

# Maps every reasonable alias → the exact string used in the shapefile
# ("CONTINENT" column in Natural Earth).
_CONTINENT_ALIASES: dict[str, str] = {
    "africa": "Africa",
    "antarctica": "Antarctica",
    "asia": "Asia",
    "europe": "Europe",
    "north america": "North America",
    "northamerica": "North America",
    "na": "North America",
    "south america": "South America",
    "southamerica": "South America",
    "sa": "South America",
    "oceania": "Oceania",
    "australia": "Oceania",  # common alias
    "seven seas (open ocean)": "Seven seas (open ocean)",
}

# ---------------------------------------------------------------------------
# Module-level cache  (populated lazily on first use, or eagerly via warm_up)
# ---------------------------------------------------------------------------

_WORLD_GDF: Optional[gpd.GeoDataFrame] = None
_WORLD_GEOMS_4326: Optional[gpd.GeoSeries] = None
_NATION_CENTERS: Optional[dict[str, tuple[float, float]]] = None
_DARK_RGBA: Optional[np.ndarray] = None  # float32
_SPEC_RGBA: Optional[np.ndarray] = None  # float32


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_shapefile() -> None:
    """Download and unzip the Natural Earth shapefile if not present."""
    if _SHAPEFILE_PATH.exists():
        return
    log.info("Downloading Natural Earth 110m shapefile …")
    _SHAPEFILE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = ASSETS_DIR / "ne_110m.zip"
    urllib.request.urlretrieve(_SHAPEFILE_URL, str(zip_path))
    with zipfile.ZipFile(str(zip_path)) as z:
        z.extractall(str(_SHAPEFILE_DIR))
    try:
        zip_path.unlink()
    except Exception:
        pass
    log.info("Shapefile ready.")


def _get_world() -> tuple[
    gpd.GeoDataFrame,
    gpd.GeoSeries,
    dict[str, tuple[float, float]],
]:
    """
    Return the cached (world GeoDataFrame, WGS-84 geometries, center map).

    Loads from disk on the first call; all subsequent calls return the cached
    objects immediately with zero I/O or reprojection overhead.
    """
    global _WORLD_GDF, _WORLD_GEOMS_4326, _NATION_CENTERS

    if _WORLD_GDF is None:
        _ensure_shapefile()
        log.debug("Loading shapefile …")
        _WORLD_GDF = gpd.read_file(str(_SHAPEFILE_PATH))

        # Project the full dataset once — reused for land rendering AND centers
        _WORLD_GEOMS_4326 = _WORLD_GDF.to_crs("EPSG:4326").geometry

        # Pre-compute representative points for every country.
        # representative_point() is guaranteed to be inside the polygon,
        # unlike centroid which can fall in the ocean for oddly-shaped countries.
        _NATION_CENTERS = {
            name.lower(): (
                geom.representative_point().x,
                geom.representative_point().y,
            )
            for name, geom in zip(_WORLD_GDF["NAME"], _WORLD_GEOMS_4326)
        }
        log.debug(
            "Shapefile loaded; %d countries indexed.", len(_NATION_CENTERS)
        )

    return _WORLD_GDF, _WORLD_GEOMS_4326, _NATION_CENTERS  # type: ignore[return-value]


def _get_shading_arrays() -> tuple[np.ndarray, np.ndarray]:
    """
    Return the cached (dark_rgba, spec_rgba) shading overlay arrays.

    Computed once per process using float32 arrays; every subsequent call
    returns the pre-built arrays with no allocation or arithmetic overhead.

    Returns:
        dark_rgba: ``(H, W, 4)`` float32 — black with varying alpha
        spec_rgba: ``(H, W, 4)`` float32 — white with varying alpha
    """
    global _DARK_RGBA, _SPEC_RGBA

    if _DARK_RGBA is not None:
        return _DARK_RGBA, _SPEC_RGBA  # type: ignore[return-value]

    log.debug("Pre-computing shading arrays (size=%d) …", _SHADE_SIZE)
    s = _SHADE_SIZE
    ax_vals = np.linspace(-1, 1, s, dtype=np.float32)
    xx, yy = np.meshgrid(ax_vals, ax_vals)
    r2 = xx**2 + yy**2
    inside = r2 <= 1.0

    nz = np.where(inside, np.sqrt(np.clip(1 - r2, 0, 1)), 0).astype(np.float32)

    lx, ly, lz = _LIGHT
    ln = np.sqrt(lx**2 + ly**2 + lz**2)
    lx, ly, lz = lx / ln, ly / ln, lz / ln

    dot = np.clip(xx * lx + yy * ly + nz * lz, 0, 1)
    limb = np.where(inside, (r2**2.5) * _LIMB_STRENGTH, 0).astype(np.float32)
    shadow = np.where(inside, (1 - dot) * _SHADOW_STRENGTH, 0).astype(
        np.float32
    )

    dark_alpha = np.clip(limb + shadow, 0, _DARK_CAP).astype(np.float32)
    _DARK_RGBA = np.zeros((s, s, 4), dtype=np.float32)
    _DARK_RGBA[..., 3] = dark_alpha

    refl_z = (2 * dot * nz - lz).astype(np.float32)
    spec = np.where(
        inside,
        np.clip(refl_z, 0, 1) ** _SPEC_SHININESS * _SPEC_STRENGTH,
        0,
    ).astype(np.float32)
    _SPEC_RGBA = np.ones((s, s, 4), dtype=np.float32)
    _SPEC_RGBA[..., 3] = spec

    return _DARK_RGBA, _SPEC_RGBA


def _composite_shading(fig: plt.Figure, ax: plt.Axes) -> None:
    """
    Overlay 3-D shading (dark + specular) on the globe axes.

    Renders into an inset axes that shares the same bounding box as *ax*
    but lives in axes-fraction space so the overlays are clipped to the
    circular globe boundary automatically.
    """
    dark_rgba, spec_rgba = _get_shading_arrays()

    ax_pos = ax.get_position()
    inset = fig.add_axes(ax_pos, zorder=10)
    inset.set_xlim(0, 1)
    inset.set_ylim(0, 1)
    inset.axis("off")
    inset.set_facecolor("none")

    kw = dict(
        extent=[0, 1, 0, 1],
        origin="upper",
        aspect="auto",
        interpolation="bilinear",
    )
    inset.imshow(dark_rgba, zorder=1, **kw)
    inset.imshow(spec_rgba, zorder=2, **kw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_nation_globe_image(
    nation_name: str, dummy_bytes_check_country_only: bool = False
) -> Optional[bytes]:
    """
    Render a 3-D orthographic globe with *nation_name* highlighted in green.

    The globe is automatically rotated so the target country is centred.
    Shading includes Lambertian diffuse lighting, limb darkening, and a
    Phong specular highlight to give a realistic spherical appearance.

    Performance:
        The first call triggers shapefile loading and shading pre-computation
        (~2-3 s total).  All subsequent calls reuse cached data and complete
        in ~0.3-0.6 s.  Call :func:`warm_up` at bot startup to pay this cost
        up-front before any user request arrives.

    Args:
        nation_name: Country name as it appears in Natural Earth data
                     (e.g. ``"Myanmar"``, ``"Canada"``, ``"Pakistan"``).
                     The lookup is case-insensitive.

    Returns:
        PNG image as raw bytes, or ``None`` if the country was not found
        or rendering failed.

    Example::

        data = get_nation_globe_image("Japan")
        with open("japan_globe.png", "wb") as f:
            f.write(data)
    """
    try:
        world, world_geoms_4326, nation_centers = _get_world()

        key = nation_name.strip().lower()
        center = nation_centers.get(key)
        if center is None:
            log.debug("Nation %r not found in shapefile.", nation_name)
            return None

        if dummy_bytes_check_country_only:
            return b"true"

        center_lon, center_lat = center
        nation_mask = world["NAME"].str.lower() == key
        nation_geoms = world.loc[nation_mask, "geometry"]

        log.debug(
            "Globe centred on %s: lon=%.1f lat=%.1f",
            nation_name,
            center_lon,
            center_lat,
        )

        proj = ccrs.Orthographic(
            central_longitude=center_lon,
            central_latitude=center_lat,
        )
        fig = plt.figure(figsize=(8, 8), facecolor="white")
        ax = fig.add_subplot(1, 1, 1, projection=proj)
        ax.set_global()

        # Ocean background
        ax.set_facecolor(_OCEAN_COLOR)

        # Land from cached shapefile — no cfeature network calls
        ax.add_geometries(
            world_geoms_4326,
            crs=ccrs.PlateCarree(),
            facecolor=_LAND_COLOR,
            edgecolor=_BORDER_COLOR,
            linewidth=0.4,
            zorder=1,
        )

        # Graticule (lat/lon grid — key to the 3-D illusion)
        gl = ax.gridlines(
            crs=ccrs.PlateCarree(),
            linewidth=0.4,
            color=_GRID_COLOR,
            alpha=0.5,
            linestyle="-",
            zorder=3,
        )
        gl.xlocator = plt.FixedLocator(range(-180, 181, 20))
        gl.ylocator = plt.FixedLocator(range(-90, 91, 20))

        # Highlighted nation
        ax.add_geometries(
            nation_geoms,
            crs=ccrs.PlateCarree(),
            facecolor=_HIGHLIGHT_COLOR,
            edgecolor=_HIGHLIGHT_EDGE,
            linewidth=0.8,
            zorder=4,
        )

        # 3-D shading overlay (uses cached arrays — zero recomputation cost)
        _composite_shading(fig, ax)

        # Globe border
        ax.spines["geo"].set_linewidth(1.2)

        # Encode to PNG bytes
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=_OUTPUT_DPI, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.getvalue()

    except Exception:
        log.exception("Failed to render globe for %r", nation_name)
        plt.close("all")
        return None


def get_random_nation_name(continent: Optional[str] = None) -> Optional[str]:
    """
    Return a random country name from the Natural Earth dataset.

    Args:
        continent: Optional continent filter.  Case-insensitive and accepts
                   common aliases (e.g. ``"asia"``, ``"North America"``,
                   ``"na"``, ``"australia"``).  If *None*, any country
                   on Earth may be returned.

    Returns:
        A country name string (suitable for passing to
        :func:`get_nation_globe_image`), or ``None`` if *continent* was
        provided but did not match any known continent name or returned
        zero countries.

    Example::

        name = get_random_nation_name()              # any country
        name = get_random_nation_name("Asia")        # Asian country
        name = get_random_nation_name("na")          # North American country
        name = get_random_nation_name("Narnia")      # → None (unknown)
    """
    try:
        world, _, _ = _get_world()

        if continent is not None:
            key = " ".join(continent.strip().lower().split())
            canonical = _CONTINENT_ALIASES.get(key)

            if canonical is None:
                log.warning(
                    "Unknown continent %r. Valid values: %s",
                    continent,
                    sorted(set(_CONTINENT_ALIASES.values())),
                )
                return None

            subset = world[world["CONTINENT"] == canonical]
            if subset.empty:
                log.warning("No countries found for continent %r.", canonical)
                return None

            return random.choice(subset["NAME"].tolist())

        return random.choice(world["NAME"].tolist())

    except Exception:
        log.exception("Failed to pick a random nation name.")
        return None


def warm_up() -> None:
    """
    Pre-load all cached data eagerly (shapefile + shading arrays).

    Call this once at bot startup so the first user request doesn't pay
    the cold-start penalty.  Safe to call multiple times — subsequent calls
    are no-ops.

    Example (Kurigram)::

        async def on_startup(bot):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, warm_up)
    """
    log.info("Warming up globe renderer …")
    _get_world()
    _get_shading_arrays()
    log.info("Globe renderer ready.")


if __name__ == "__main__":
    warm_up()
    name = get_random_nation_name("North America")
    print(f"Random North American country: {name}")
    data = get_nation_globe_image(name)
    print(f"Rendered {len(data):,} bytes for {name!r}")
