# Antigravity Rules & Guidelines - SenoNetwork

## Permessi Git e Version Control
- **Autonomia di Commit e Push**: Antigravity ha il permesso esplicito e permanente di eseguire autonomamente `git commit` e `git push` su GitHub per questo progetto, senza dover richiedere conferma o autorizzazione manuale all'utente prima di ogni operazione.
- **Convenzione Commit**: Utilizzare lo standard **Conventional Commits** (es. `feat:`, `fix:`, `refactor:`, `test:`, `docs:`) con descrizioni chiare, concise e informative nel corpo del commit.
- **Verifiche prima del commit/push**:
  - Assicurarsi che i test unitari pertinenti vengano eseguiti con successo.
  - Verificare che non vengano inclusi file temporanei, cache o credenziali segrete (`.env`, log, token, ecc.).
- **Gestione dei Branch**:
  - Eseguire il push sul branch di lavoro corrente (`master` o feature branch).
  - Non eseguire push distruttivi (`git push --force`) su rami condivisi a meno che non sia specificamente richiesto dall'utente.
