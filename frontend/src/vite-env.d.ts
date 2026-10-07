/// <reference types="vite/client" />

// Env vars MABLOP reads. VITE_MABLOP_API_URL = Lambda Function URL base.
interface ImportMetaEnv {
  readonly VITE_MABLOP_API_URL: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
