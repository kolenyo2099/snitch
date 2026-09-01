import { useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { GeoJsonLayer } from "@deck.gl/layers";
import { api } from "../api";

const STYLE: any = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm" }],
};

function bounds(geojson: any): [number, number, number, number] {
  const c: number[][] = [];
  const walk = (x: any) =>
    typeof x[0] === "number" ? c.push(x as number[]) : (x as any[]).forEach(walk);
  walk(geojson.coordinates);
  const xs = c.map((p) => p[0]), ys = c.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/** Change overlays are never shown without the mask layer available as a toggle (§13.3). */
export function MapView({ aoi, change, maskArtifactId, maskLayers, height = 520 }: {
  aoi: any; change?: any; maskArtifactId?: number | null;
  maskLayers?: Record<string, number>; height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map>();
  const deckRef = useRef<MapboxOverlay>();
  const [showChange, setShowChange] = useState(true);
  const [showMask, setShowMask] = useState(false);
  // Tiles take a few seconds. Until they arrive the map is an unexplained black
  // rectangle that reads as a broken basemap rather than as a pending one.
  const [tilesReady, setTilesReady] = useState(false);
  const [selectedMask, setSelectedMask] = useState("all masked pixels");
  const selectedArtifact = selectedMask === "all masked pixels"
    ? maskArtifactId : maskLayers?.[selectedMask];

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = new maplibregl.Map({ container: ref.current, style: STYLE, center: [0, 0], zoom: 2 });
    map.addControl(new maplibregl.NavigationControl(), "top-left");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: "metric" }), "bottom-left");
    map.once("idle", () => setTilesReady(true));
    const deck = new MapboxOverlay({ layers: [] });
    map.addControl(deck as any);
    mapRef.current = map;
    deckRef.current = deck;
    return () => { map.remove(); mapRef.current = undefined; };
  }, []);

  useEffect(() => {
    const map = mapRef.current, deck = deckRef.current;
    if (!map || !deck || !aoi) return;
    const [w, s, e, n] = bounds(aoi);
    map.fitBounds([[w, s], [e, n]], { padding: 48, duration: 0 });
    deck.setProps({
      layers: [
        new GeoJsonLayer({
          id: "aoi", data: { type: "Feature", geometry: aoi, properties: {} },
          stroked: true, filled: false, getLineColor: [111, 195, 160], lineWidthMinPixels: 2,
        }),
        showChange && change
          ? new GeoJsonLayer({
              id: "change", data: { type: "Feature", geometry: change, properties: {} },
              stroked: true, filled: true, getFillColor: [224, 92, 75, 110],
              getLineColor: [224, 92, 75], lineWidthMinPixels: 1.5,
            })
          : null,
      ].filter(Boolean) as any,
    });
  }, [aoi, change, showChange, showMask]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    let cancelled = false;
    const render = async () => {
      if (map.getLayer("mask-overlay")) map.removeLayer("mask-overlay");
      if (map.getSource("mask-overlay")) map.removeSource("mask-overlay");
      if (!showMask || !selectedArtifact) return;
      const overlay = await api.artifactOverlay(selectedArtifact);
      if (cancelled) return;
      map.addSource("mask-overlay", { type: "image", url: overlay.url,
                                      coordinates: overlay.coordinates });
      map.addLayer({ id: "mask-overlay", type: "raster", source: "mask-overlay",
                     paint: { "raster-opacity": 0.82 } });
    };
    if (map.loaded()) render(); else map.once("load", render);
    return () => { cancelled = true; };
  }, [showMask, selectedArtifact]);

  return (
    <div style={{ position: "relative" }}>
      <div className="map" style={{ height }} ref={ref}>
        {!tilesReady && <div className="map-loading">Loading map tiles…</div>}
      </div>
      <button style={{ position: "absolute", left: 10, bottom: 34, zIndex: 5 }}
              onClick={() => {
                if (!aoi || !mapRef.current) return;
                const [w, s2, e, n] = bounds(aoi);
                mapRef.current.fitBounds([[w, s2], [e, n]], { padding: 48 });
              }}>
        Zoom to area
      </button>
      <div className="map-controls">
        <label style={{ display: "block" }}>
          <input type="checkbox" checked={showChange} disabled={!change}
                 onChange={(e) => setShowChange(e.target.checked)} /> change footprint
        </label>
        <label style={{ display: "block" }}>
          <input type="checkbox" checked={showMask} disabled={!maskArtifactId}
                 onChange={(e) => setShowMask(e.target.checked)} /> masked pixels
        </label>
        {showMask && maskLayers && Object.keys(maskLayers).length > 0 && (
          <select aria-label="Mask layer" value={selectedMask}
                  onChange={(e) => setSelectedMask(e.target.value)}>
            <option>all masked pixels</option>
            {Object.keys(maskLayers).map((name) => <option key={name}>{name}</option>)}
          </select>
        )}
        {!maskArtifactId && <div className="tiny muted">no mask raster for this run</div>}
      </div>
    </div>
  );
}

/** Draw an AOI. terra-draw is loaded lazily so the map still works if it fails. */
export function DrawMap({ onChange, onPlace, height = 460 }:
  { onChange: (geojson: any | null) => void;
    onPlace?: (name: string) => void; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map>();
  const timer = useRef<any>();
  const lastKey = useRef<string | null>(null);
  const [tilesReady, setTilesReady] = useState(false);
  // Which tool is armed, and how to use it. Rectangle is click-corner-then-click-
  // opposite-corner; dragging just pans, so without this the first attempt fails
  // silently and the tool looks broken.
  const [mode, setModeState] = useState<"polygon" | "rectangle" | null>(null);
  const [place, setPlace] = useState("");
  const [results, setResults] = useState<any[] | null>(null);
  const [searching, setSearching] = useState(false);
  // the map effect runs once; keep the latest callback reachable from inside it
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = new maplibregl.Map({ container: ref.current, style: STYLE, center: [4.9, 52.4], zoom: 8 });
    map.addControl(new maplibregl.NavigationControl(), "top-left");
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 110, unit: "metric" }), "bottom-left");
    map.once("idle", () => setTilesReady(true));
    mapRef.current = map;
    let draw: any;
    map.on("load", async () => {
      try {
        const { TerraDraw, TerraDrawPolygonMode, TerraDrawRectangleMode } = await import("terra-draw");
        const { TerraDrawMapLibreGLAdapter } = await import("terra-draw-maplibre-gl-adapter");
        draw = new TerraDraw({
          adapter: new TerraDrawMapLibreGLAdapter({ map }),
          modes: [new TerraDrawPolygonMode(), new TerraDrawRectangleMode()],
        });
        draw.start();
        draw.setMode("polygon");
        setModeState("polygon");
        (map as any).__draw = draw;
        // "change" fires on every vertex drag. Each one used to launch a full
        // coverage check, so a single edit produced a burst of overlapping requests.
        // Settle first, then ask once — and never ask twice for the same shape.
        const emit = () => {
          const fs = draw.getSnapshot().filter((f: any) => f.geometry.type === "Polygon");
          const geometry = fs.length ? fs[fs.length - 1].geometry : null;
          const key = JSON.stringify(geometry);
          if (key === lastKey.current) return;
          lastKey.current = key;
          onChangeRef.current(geometry);
        };
        const settle = () => {
          clearTimeout(timer.current);
          timer.current = setTimeout(emit, 400);
        };
        draw.on("finish", settle);
        draw.on("change", settle);
      } catch (e) {
        console.warn("terra-draw unavailable; paste GeoJSON instead", e);
      }
    });
    return () => {
      clearTimeout(timer.current);
      draw?.stop?.(); map.remove(); mapRef.current = undefined;
    };
  }, []);

  const setMode = (m: "polygon" | "rectangle") => {
    (mapRef.current as any)?.__draw?.setMode(m);
    setModeState(m);
  };

  /* Panning the whole globe by hand to find your own site is not a search. Nominatim
   * needs no key and is the same project already serving the basemap tiles. */
  const search = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!place.trim()) return;
    setSearching(true); setResults(null);
    try {
      const r = await fetch(
        "https://nominatim.openstreetmap.org/search?format=json&limit=5&q="
        + encodeURIComponent(place), { headers: { "accept-language": "en" } });
      setResults(r.ok ? await r.json() : []);
    } catch { setResults([]); }
    finally { setSearching(false); }
  };

  const goTo = (r: any) => {
    const [s, n, w, e] = (r.boundingbox || []).map(Number);
    if ([s, n, w, e].every(Number.isFinite))
      mapRef.current?.fitBounds([[w, s], [e, n]], { padding: 40 });
    else
      mapRef.current?.flyTo({ center: [+r.lon, +r.lat], zoom: 12 });
    const short = r.display_name.split(",")[0];
    setResults(null); setPlace(short); onPlace?.(short);
  };

  return (
    <div style={{ position: "relative" }}>
      <div className="map" style={{ height }} ref={ref}>
        {!tilesReady && <div className="map-loading">Loading map tiles…</div>}
      </div>

      <form className="geo-search" onSubmit={search}>
        <input value={place} onChange={(e) => setPlace(e.target.value)}
               placeholder="Find a place…" aria-label="Search for a place" />
        <button type="submit" disabled={!place.trim() || searching}>
          {searching ? "Searching…" : "Go"}
        </button>
      </form>
      {results && (
        <div className="geo-results" role="listbox" aria-label="Place results">
          {results.length
            ? results.map((r) => (
                <button key={r.place_id} type="button" onClick={() => goTo(r)}>
                  {r.display_name}
                </button>))
            : <p className="tiny muted" style={{ padding: 8, margin: 0 }}>
                No place matched that. You can still pan the map or paste a geometry below.
              </p>}
        </div>
      )}

      <div className="map-controls row">
        <button aria-pressed={mode === "polygon"}
                className={mode === "polygon" ? "primary" : ""}
                onClick={() => setMode("polygon")}>Polygon</button>
        <button aria-pressed={mode === "rectangle"}
                className={mode === "rectangle" ? "primary" : ""}
                onClick={() => setMode("rectangle")}>Rectangle</button>
        <button onClick={() => {
          clearTimeout(timer.current);
          lastKey.current = null;
          (mapRef.current as any)?.__draw?.clear();
          setResults(null);
          onChange(null);
        }}>
          Clear
        </button>
      </div>

      {tilesReady && mode && (
        <p className="map-hint">
          {mode === "rectangle"
            ? "Click one corner, then click the opposite corner. Dragging pans the map."
            : "Click each corner of the area, then click the first point again to close it."}
        </p>
      )}
    </div>
  );
}

/** Identical stretch on both frames, so the comparison cannot manufacture change.
 *  Before sits on the left, after on the right — and the divider itself is the
 *  handle: drag anywhere on the frame, or use the slider / arrow keys. */
export function SwipeCompare({ before, after }: { before?: string; after?: string }) {
  const [pos, setPos] = useState(50);            // divider position, % from the left
  const [dragging, setDragging] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);
  if (!before || !after) return <p className="muted">No chip pair for this alert.</p>;
  const p = Math.min(Math.max(pos, 0.5), 99.5);  // keep the clipped layer non-degenerate
  const moveTo = (clientX: number) => {
    const el = wrapRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (!r.width) return;
    setPos(Math.min(100, Math.max(0, ((clientX - r.left) / r.width) * 100)));
  };
  return (
    <div>
      <div ref={wrapRef}
           className={`swipe-wrap${dragging ? " dragging" : ""}`}
           onPointerDown={(e) => {
             e.currentTarget.setPointerCapture(e.pointerId);
             setDragging(true);
             moveTo(e.clientX);
           }}
           onPointerMove={(e) => { if (dragging) moveTo(e.clientX); }}
           onPointerUp={() => setDragging(false)}
           onPointerCancel={() => setDragging(false)}>
        <img src={before} alt="Before" loading="lazy" draggable={false} />
        <div className="after" style={{ left: `${p}%` }}>
          <img src={after} alt="After" loading="lazy" draggable={false}
               style={{ width: `${10000 / (100 - p)}%`, maxWidth: "none",
                        marginLeft: `${-(100 * p) / (100 - p)}%` }} />
        </div>
        <div className="swipe-handle" style={{ left: `${p}%` }}
             role="slider" tabIndex={0}
             aria-label="Comparison divider" aria-valuemin={0} aria-valuemax={100}
             aria-valuenow={Math.round(pos)}
             onKeyDown={(e) => {
               const step = e.key === "ArrowLeft" ? -3 : e.key === "ArrowRight" ? 3 : 0;
               if (!step) return;
               e.preventDefault();
               setPos((v) => Math.min(100, Math.max(0, v + step)));
             }} />
      </div>
      <input type="range" min={0} max={100} value={Math.round(pos)}
             aria-label="Comparison position" style={{ width: "100%" }}
             onChange={(e) => setPos(+e.target.value)} />
      <p className="tiny muted">
        Drag the divider — before on the left, after on the right. Both frames use the
        same stretch, printed on each image; independently stretched pairs manufacture
        apparent change and are never produced.
      </p>
    </div>
  );
}
