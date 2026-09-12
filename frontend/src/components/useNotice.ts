"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export default function useNotice(page: string) {
  const [notice, setValue] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearTimer = useCallback(() => {
    if (timer.current !== null) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  const setNotice = useCallback((message: string) => {
    clearTimer();
    setValue(message);
    if (message) timer.current = setTimeout(() => { timer.current = null; setValue(""); }, 3000);
  }, [clearTimer]);
  useEffect(() => { setNotice(""); return clearTimer; }, [page, setNotice, clearTimer]);
  return [notice, setNotice] as const;
}
