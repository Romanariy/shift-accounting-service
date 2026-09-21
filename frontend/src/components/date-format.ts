const weekdays = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];

export function dateLabel(value?:string|null, fallback="—"):string {
  if(!value)return fallback;
  const iso=value.slice(0,10);
  if(!/^\d{4}-\d{2}-\d{2}$/.test(iso))return value;
  const date=new Date(iso+"T00:00:00Z");
  if(Number.isNaN(date.getTime()))return fallback;
  return `${iso.split("-").reverse().join(".")} (${weekdays[date.getUTCDay()]})`;
}

export function dateTimeLabel(value?:string|null):string {
  if(!value)return "—";
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return "—";
  const zone="Asia/Yekaterinburg";
  const iso=new Intl.DateTimeFormat("sv-SE",{timeZone:zone,year:"numeric",month:"2-digit",day:"2-digit"}).format(date);
  const time=new Intl.DateTimeFormat("ru-RU",{timeZone:zone,hour:"2-digit",minute:"2-digit",second:"2-digit"}).format(date);
  return `${dateLabel(iso)}, ${time}`;
}
