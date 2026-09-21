export type Item = { id: number; [key: string]: any };
export type Bootstrap = {
  services: Item[]; rates: Item[]; accruals: Item[]; preferences: Item[];
  contacts: Item[]; organizations: Item[]; settings: Item;
  employees: Item[]; organization_list: Item[];
  offer_config?: {enabled:boolean;chat_id:number|null;thread_id:number|null;claimed_thread_id?:number|null;released_thread_id?:number|null};
};
export type Field = { key: string; label: string; type?: string; required?: boolean; hint?: string; options?: { value: string | number; label: string }[] };
export type Editor = { title: string; resource: string; values: Record<string, any>; fields: Field[]; legacy?: boolean };
