import { createContext, useContext } from 'react';

export interface AppConfig {
  catalog: string;
  user: string;
}

export const ConfigContext = createContext<AppConfig>({ catalog: '', user: '' });
export const useConfig = () => useContext(ConfigContext);
