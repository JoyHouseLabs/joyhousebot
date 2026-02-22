export function rpcError(code, message, data) {
  return data ? { code, message, data } : { code, message };
}

