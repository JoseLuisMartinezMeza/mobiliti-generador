import {useSyncExternalStore} from "react";

import {officialProvidersSnapshot, subscribeOfficialProviders} from "./officialProviders.js";

export function useOfficialProviders() {
  return useSyncExternalStore(subscribeOfficialProviders, officialProvidersSnapshot);
}

// Menu con los proveedores de la plantilla; texto libre mientras la lista no este disponible.
export default function OfficialProviderField({value, onChange, ...props}) {
  const {providers} = useOfficialProviders();
  if (!providers.length) {
    return <input {...props} value={value} onChange={(event) => onChange(event.target.value)} />;
  }
  return (
    <select {...props} value={value} onChange={(event) => onChange(event.target.value)}>
      <option value="">Selecciona un proveedor</option>
      {/* Un proveedor guardado que no es oficial se conserva hasta que el usuario lo cambie. */}
      {value && !providers.includes(value) ? <option value={value}>{value} (no oficial)</option> : null}
      {providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}
    </select>
  );
}
