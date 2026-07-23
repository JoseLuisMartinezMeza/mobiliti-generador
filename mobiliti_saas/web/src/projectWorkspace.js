export function createProjectOperationId(cryptoApi = globalThis.crypto) {
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  if (typeof cryptoApi?.getRandomValues !== "function") {
    throw new Error("El navegador no puede generar un identificador de operación seguro.");
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export function createProjectLoadGuard() {
  let mounted = true;
  let currentEpoch = 0;
  return {
    begin() {
      currentEpoch += 1;
      return currentEpoch;
    },
    canApply(epoch) {
      return mounted && epoch === currentEpoch;
    },
    isMounted() {
      return mounted;
    },
    dispose() {
      mounted = false;
      currentEpoch += 1;
    },
  };
}
