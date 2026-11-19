-- Run after supabase_setup.sql, before enabling the Mini App on the bot.
begin;

create table if not exists public.stock_watchlist (
    chat_id text not null,
    product_name text not null,
    created_at timestamptz not null default now(),
    primary key (chat_id, product_name)
);
create index if not exists stock_watchlist_product_idx on public.stock_watchlist (product_name);

create table if not exists public.watch_restock_alerts (
    chat_id text not null,
    event_id bigint not null references public.product_events (id) on delete cascade,
    product_name text not null,
    next_attempt_at timestamptz not null default now(),
    primary key (chat_id, event_id),
    foreign key (chat_id, product_name)
        references public.stock_watchlist (chat_id, product_name) on delete cascade
);
create index if not exists watch_restock_alerts_due_idx
    on public.watch_restock_alerts (next_attempt_at);
create index if not exists product_events_last_restock_idx
    on public.product_events (nama, detected_at desc) where event_type = 'restock';

alter table public.stock_watchlist enable row level security;
alter table public.watch_restock_alerts enable row level security;
grant select, insert, delete on public.stock_watchlist to service_role;
grant select, insert, update, delete on public.watch_restock_alerts to service_role;

create or replace function public.enqueue_watch_restock_alert()
returns trigger language plpgsql set search_path = '' as $$
begin
    insert into public.watch_restock_alerts (chat_id, event_id, product_name)
    select chat_id, new.id, product_name
    from public.stock_watchlist where product_name = new.nama
    on conflict (chat_id, event_id) do nothing;
    return new;
end;
$$;

drop trigger if exists enqueue_watch_restock_alert on public.product_events;
create trigger enqueue_watch_restock_alert after insert on public.product_events
for each row when (new.event_type = 'restock')
execute function public.enqueue_watch_restock_alert();

create or replace function public.get_mini_watchlist(p_chat_id text)
returns table (product_name text, last_restock timestamptz)
language sql stable set search_path = '' as $$
    select watched.product_name, latest.detected_at
    from public.stock_watchlist as watched
    left join lateral (
        select event.detected_at from public.product_events as event
        where event.nama = watched.product_name and event.event_type = 'restock'
        order by event.detected_at desc limit 1
    ) as latest on true
    where watched.chat_id = p_chat_id
    order by watched.created_at desc, watched.product_name;
$$;

create or replace function public.set_mini_watch(
    p_chat_id text, p_product_name text, p_enabled boolean
) returns timestamptz
language plpgsql set search_path = '' as $$
begin
    if p_enabled then
        insert into public.stock_watchlist (chat_id, product_name)
        values (p_chat_id, p_product_name)
        on conflict (chat_id, product_name) do nothing;
        return (
            select detected_at from public.product_events
            where nama = p_product_name and event_type = 'restock'
            order by detected_at desc limit 1
        );
    else
        delete from public.stock_watchlist
        where chat_id = p_chat_id and product_name = p_product_name;
        return null;
    end if;
end;
$$;

-- Lease pending messages. A stopped worker can be replaced after the lease expires.
create or replace function public.claim_watch_restock_alerts()
returns table (chat_id text, event_id bigint, product_name text, detected_at timestamptz)
language plpgsql set search_path = '' as $$
begin
    delete from public.watch_restock_alerts as alert
    using public.product_events as event
    where event.id = alert.event_id and event.detected_at < now() - interval '1 day';

    return query
    with due as (
        select alert.chat_id, alert.event_id
        from public.watch_restock_alerts as alert
        join public.user_settings as account on account.chat_id = alert.chat_id
        where alert.next_attempt_at <= now()
          and (account.role = 'admin' or (
              coalesce(account.plan_type, 'none') <> 'suspended'
              and nullif(account.valid_until::text, '')::timestamptz > now()
          ))
        order by alert.next_attempt_at, alert.event_id
        limit 50
        for update of alert skip locked
    )
    update public.watch_restock_alerts as alert
    set next_attempt_at = now() + interval '5 minutes'
    from due, public.product_events as event
    where alert.chat_id = due.chat_id and alert.event_id = due.event_id
      and event.id = alert.event_id
    returning alert.chat_id, alert.event_id, alert.product_name, event.detected_at;
end;
$$;

revoke execute on function public.enqueue_watch_restock_alert(),
    public.get_mini_watchlist(text), public.set_mini_watch(text, text, boolean), public.claim_watch_restock_alerts()
    from public, anon, authenticated;
grant execute on function public.get_mini_watchlist(text), public.set_mini_watch(text, text, boolean),
    public.claim_watch_restock_alerts() to service_role;

notify pgrst, 'reload schema';
commit;
