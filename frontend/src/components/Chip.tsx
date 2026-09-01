import { useState } from "react";
import { artifactUrl } from "../api";

/**
 * A rendered satellite chip. Every use goes through here so a missing artifact
 * says so: a bare <img> to a 404 renders the browser's broken-image glyph, which
 * looks like a bug in the picture rather than a missing file, and a chip that
 * simply isn't there renders nothing at all with no explanation.
 */
export function ChipImage({ id, alt, className, style }: {
  id?: number | null; alt: string; className?: string; style?: React.CSSProperties;
}) {
  const [failed, setFailed] = useState(false);
  if (!id || failed)
    return (
      <div className={className} style={{
        display: "flex", alignItems: "center", justifyContent: "center",
        background: "var(--panel2)", border: "1px dashed var(--line)",
        borderRadius: 6, color: "var(--faint)", fontSize: 11, textAlign: "center",
        minHeight: 90, padding: 8, aspectRatio: id ? undefined : "1", ...style,
      }}>
        {id ? `${alt} image is missing from the artifact store` : `No ${alt.toLowerCase()} image`}
      </div>
    );
  return (
    <img src={artifactUrl(id)} alt={alt} loading="lazy" decoding="async"
         className={className} style={style} onError={() => setFailed(true)} />
  );
}
