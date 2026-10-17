import { ChatVectorClient } from "@chatvector/sdk";

function requiredEnvironmentVariable(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new Error(`${name} must be set in the server environment`);
  }
  return value;
}

let client: ChatVectorClient | undefined;

export function getChatVectorClient(): ChatVectorClient {
  if (!client) {
    client = new ChatVectorClient({
      baseUrl: requiredEnvironmentVariable("CHATVECTOR_BASE_URL"),
      apiKey: requiredEnvironmentVariable("CHATVECTOR_API_KEY"),
      timeoutMs: 30_000,
    });
  }
  return client;
}
