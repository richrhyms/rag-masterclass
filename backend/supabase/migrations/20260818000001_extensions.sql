-- Extensions required by the schema (design.md "Indexes / extensions (DD-3)").
create extension if not exists vector;
create extension if not exists pgcrypto; -- gen_random_uuid()
