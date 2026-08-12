import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { framingBox } from "./Labels3DPanel";

function labelMesh(id: number, x: number) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(2, 2, 2));
  mesh.name = String(id);
  mesh.position.set(x, 0, 0);
  return mesh;
}

describe("Labels3DPanel camera framing", () => {
  it("frames the soloed edge mesh instead of the loaded multi-label group", () => {
    const group = new THREE.Group();
    group.add(labelMesh(1, -50), labelMesh(2, 0), labelMesh(3, 80));

    const allCenter = framingBox(group)?.getCenter(new THREE.Vector3());
    const soloCenter = framingBox(group, 3)?.getCenter(new THREE.Vector3());

    expect(allCenter?.x).toBeCloseTo(15);
    expect(soloCenter?.x).toBeCloseTo(80);
  });

  it("does not fall back to all labels when the requested mesh is not loaded", () => {
    const group = new THREE.Group();
    group.add(labelMesh(1, 0));

    expect(framingBox(group, 9)).toBeNull();
  });
});
