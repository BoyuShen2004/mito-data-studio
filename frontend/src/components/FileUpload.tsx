import { useState } from "react";

interface Props {
  label?: string;
  accept?: string;
  onChange: (file: File | null) => void;
}

export default function FileUpload({ label = "File", accept, onChange }: Props) {
  const [name, setName] = useState<string>("");
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="file"
        accept={accept}
        onChange={(e) => {
          const file = e.target.files?.[0] ?? null;
          setName(file?.name ?? "");
          onChange(file);
        }}
      />
      {name && <small className="muted">Selected: {name}</small>}
    </label>
  );
}
