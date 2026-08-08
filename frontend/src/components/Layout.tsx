import type { ReactNode } from "react";
import Navbar from "./Navbar";

interface Props {
  children: ReactNode;
  /** Skip the centered max-width container — used by full-window viewer/editor. */
  fullBleed?: boolean;
}

export default function Layout({ children, fullBleed = false }: Props) {
  return (
    <div className={`layout-root${fullBleed ? " layout-root-bleed" : ""}`}>
      <Navbar />
      {fullBleed ? (
        <div className="full-bleed-main">{children}</div>
      ) : (
        <div className="container">{children}</div>
      )}
    </div>
  );
}
