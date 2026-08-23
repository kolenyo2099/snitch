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
  const [selectedMask, setSelectedMask] = useState("all masked pixels");
  const selectedArtifact = selectedMask === "all masked pixels"
    ? maskArtifactId : maskLayers?.[selectedMask];

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = new maplibregl.Map({ container: ref.current, style: STYLE, center: [0, 0], zoom: 2 });
    map.addControl(new maplibregl.NavigationControl(), "top-left");
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
      <div className="map" style={{ height }} ref={ref} />
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
export function DrawMap({ onChange, height = 460 }:
  { onChange: (geojson: any | null) => void; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map>();
  const timer = useRef<any>();
  const lastKey = useRef<string | null>(null);
  // the map effect runs once; keep the latest callback reachable from inside it
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    const map = new maplibregl.Map({ container: ref.current, style: STYLE, center: [4.9, 52.4], zoom: 8 });
    map.addControl(new maplibregl.NavigationControl(), "top-left");
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

  const setMode = (m: string) => (mapRef.current as any)?.__draw?.setMode(m);

  return (
    <div style={{ position: "relative" }}>
      <div className="map" style={{ height }} ref={ref} />
      <div className="map-controls row">
        <button onClick={() => setMode("polygon")}>Polygon</button>
        <button onClick={() => setMode("rectangle")}>Rectangle</button>
        <button onClick={() => {
          clearTimeout(timer.current);
          lastKey.current = null;
          (mapRef.current as any)?.__draw?.clear();
          onChange(null);
        }}>
          Clear
        </button>
      </div>
    </div>
  );
}

/** Identical stretch on both frames, so the comparison cannot manufacture change. */
export function SwipeCompare({ before, after }: { before?: string; after?: string }) {
  const [pos, setPos] = useState(50);
  if (!before || !after) return <p className="muted">No chip pair for this alert.</p>;
  return (
    <div>
      <div className="swipe-wrap">
        <img src={before} alt="before" />
        <div className="after" style={{ width: `${pos}%` }}>
          <img src={after} alt="after" style={{ width: `${100 / (pos / 100)}%`, maxWidth: "none" }} />
        </div>
        <div className="swipe-handle" style={{ left: `${pos}%` }} />
      </div>
      <input type="range" min={0} max={100} value={pos} style={{ width: "100%" }}
             onChange={(e) => setPos(+e.target.value)} />
      <p className="tiny muted">
        Both frames use the same stretch, printed on each image. Independently stretched
        pairs manufacture apparent change and are never produced.
      </p>
    </div>
  );
}
