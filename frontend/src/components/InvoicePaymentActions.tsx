"use client";
import {useEffect,useRef,useState} from "react";
import type {Item} from "./accounting-types";

export function InvoicePaymentActions({invoice,busy,onAction}:{invoice:Item;busy:boolean;onAction:(invoice:Item,cancel:boolean)=>void}) {
  if(!invoice.payment_action_allowed)return null;
  const cancel=invoice.payment_state==="paid";
  return <button className="text-button" disabled={busy} onClick={()=>onAction(invoice,cancel)}>
    {cancel?"Отменить подтверждение":"Подтвердить оплату"}
  </button>;
}

export default function InvoicePaymentDialog({invoice,cancel,onClose,onSave}:{
  invoice:Item;cancel:boolean;onClose:()=>void;onSave:(payload:Record<string,unknown>)=>Promise<void>;
}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [reason,setReason]=useState(""),[busy,setBusy]=useState(false),[error,setError]=useState("");
  useEffect(()=>{const node=dialog.current;node?.showModal();return()=>node?.close();},[]);
  const title=cancel?"Отменить подтверждение оплаты":"Подтвердить оплату";
  return <dialog ref={dialog} className="edit-dialog" aria-labelledby="invoice-payment-title" onCancel={e=>{if(busy)e.preventDefault();else onClose();}}>
    <form onSubmit={async e=>{e.preventDefault();if(busy)return;setError("");
      if(cancel&&!reason.trim()){setError("Укажите причину отмены.");return;}
      setBusy(true);try{await onSave({version:invoice.version,payment_revision:invoice.payment_revision,...(cancel?{reason:reason.trim()}:{})});onClose();}
      catch(e){setError(e instanceof Error?e.message:"Не удалось изменить оплату.");}finally{setBusy(false);}
    }}>
      <div className="dialog-heading"><h2 id="invoice-payment-title">{title}</h2><button type="button" className="icon-button" aria-label="Закрыть" disabled={busy} onClick={onClose}>×</button></div>
      <div className="padded"><strong>{invoice.organization_name}</strong><p>Счёт №{invoice.id} · версия {invoice.version}</p>
        <p>Сумма: {new Intl.NumberFormat("ru-RU",{style:"currency",currency:"RUB"}).format(Number(invoice.total))}</p>
        <p>{cancel?"Счёт снова будет ожидать оплаты для всех получателей организации. Кнопка «Оплатил» восстановится в доставленных сообщениях Telegram.":"Подтверждение закроет ожидание оплаты для всех получателей организации."}</p>
        {cancel&&<label>Причина отмены<textarea required maxLength={2000} value={reason} disabled={busy} onChange={e=>setReason(e.target.value)}/></label>}
        {error&&<p role="alert" className="alert">{error}</p>}
        <div className="actions"><button className="button" disabled={busy}>{busy?"Сохраняем…":title}</button><button type="button" className="button secondary" disabled={busy} onClick={onClose}>Закрыть</button></div>
      </div>
    </form>
  </dialog>;
}
