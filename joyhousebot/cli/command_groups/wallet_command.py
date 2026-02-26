"""Wallet command group extracted from legacy commands.py."""

from __future__ import annotations

import getpass

import typer
from rich.console import Console
from rich.table import Table


def register_wallet_commands(app: typer.Typer, console: Console) -> None:
    """Register wallet command group."""
    wallet_app = typer.Typer(help="Wallet: init, list, change password, set default, show private key")
    app.add_typer(wallet_app, name="wallet")

    @wallet_app.command("init")
    def wallet_init(
        set_default: bool = typer.Option(True, "--set-default/--no-set-default", help="Set as default wallet"),
    ) -> None:
        """Initialize a new wallet: generate EVM address and encrypted private key (requires entering password, at least 8 characters with uppercase and lowercase)."""
        from joyhousebot.identity.wallet_store import create_and_save_wallet, validate_wallet_password

        password = getpass.getpass("Set wallet password (at least 8 characters, must include uppercase and lowercase): ")
        if not password:
            raise typer.BadParameter("Password cannot be empty")
        try:
            validate_wallet_password(password)
        except ValueError as e:
            raise typer.BadParameter(str(e))
        confirm = getpass.getpass("Enter password again: ")
        if password != confirm:
            raise typer.BadParameter("Passwords do not match")
        address = create_and_save_wallet(password, set_as_default=set_default)
        console.print("[green]✓[/green] Wallet created")
        console.print(f"Address: [cyan]{address}[/cyan]")
        if set_default:
            console.print("[dim]Set as default wallet[/dim]")

    @wallet_app.command("list")
    def wallet_list() -> None:
        """List all wallets (address, chain, chain_id, is default)."""
        from joyhousebot.identity.wallet_store import list_wallets

        rows = list_wallets()
        if not rows:
            console.print("[yellow]No wallets yet, create one with joyhousebot wallet init[/yellow]")
            return
        table = Table(title="Wallet List")
        table.add_column("ID", style="dim")
        table.add_column("Address", style="cyan")
        table.add_column("Chain")
        table.add_column("chain_id")
        table.add_column("Default")
        for r in rows:
            table.add_row(
                str(r["id"]),
                r["address"],
                r["chain"],
                str(r["chain_id"]),
                "[green]Yes[/green]" if r["is_default"] else "No",
            )
        console.print(table)

    @wallet_app.command("set-default")
    def wallet_set_default(
        target: str = typer.Argument(..., help="Wallet ID or address (0x...)"),
    ) -> None:
        """Set specified wallet as default (used for signing, etc.)."""
        from joyhousebot.identity.wallet_store import set_default_wallet, list_wallets

        wallets = list_wallets()
        if not wallets:
            raise typer.BadParameter("No wallets yet")
        target = target.strip()
        if target.isdigit():
            wid = int(target)
            if not any(w["id"] == wid for w in wallets):
                raise typer.BadParameter(f"Wallet with ID={target} not found")
            set_default_wallet(wallet_id=wid)
        else:
            if not any(w["address"] == target for w in wallets):
                raise typer.BadParameter(f"Address {target} not found")
            set_default_wallet(address=target)
        console.print("[green]✓[/green] Default wallet updated")

    @wallet_app.command("change-password")
    def wallet_change_password(
        target: str = typer.Argument(None, help="钱包 ID 或地址；不填则改默认钱包"),
    ) -> None:
        """修改指定钱包的密码（需输入旧密码和新密码）。"""
        from joyhousebot.identity.wallet_store import change_wallet_password, list_wallets, get_wallet_address

        default_addr = get_wallet_address()
        if not default_addr and not target:
            raise typer.BadParameter("暂无钱包，请先 wallet init")
        display_addr = default_addr if not target else None
        if target:
            wallets = list_wallets()
            t = target.strip()
            w = next((x for x in wallets if str(x["id"]) == t or x["address"] == t), None)
            if not w:
                raise typer.BadParameter(f"未找到钱包: {target}")
            display_addr = w["address"]
        if display_addr:
            console.print("[cyan]钱包地址:[/cyan]", display_addr)
        old = getpass.getpass("当前密码: ")
        new = getpass.getpass("新密码（至少8位，须含大小写）: ")
        if not new:
            raise typer.BadParameter("新密码不能为空")
        confirm = getpass.getpass("再次输入新密码: ")
        if new != confirm:
            raise typer.BadParameter("两次新密码不一致")
        wallet_id = int(target.strip()) if target and target.strip().isdigit() else None
        address = target.strip() if target and not target.strip().isdigit() else None
        try:
            change_wallet_password(old, new, wallet_id=wallet_id, address=address)
        except ValueError as e:
            raise typer.BadParameter(str(e))
        console.print("[green]✓[/green] 密码已修改")

    @wallet_app.command("show-key")
    def wallet_show_key(
        target: str = typer.Argument(
            None,
            help="可选：钱包 ID（如 1）或地址（如 0x...）；不填则显示默认钱包的私钥",
        ),
    ) -> None:
        """输入密码后解密并显示指定钱包的地址与私钥（不指定则用默认钱包）。请勿在他人可见处使用。"""
        from joyhousebot.identity.wallet_store import decrypt_wallet, get_wallet_address, list_wallets

        if not get_wallet_address() and not target:
            raise typer.BadParameter("暂无钱包，请先 wallet init")
        display_addr = get_wallet_address() if not target else None
        if target:
            wallets = list_wallets()
            t = target.strip()
            w = next((x for x in wallets if str(x["id"]) == t or x["address"] == t), None)
            if not w:
                raise typer.BadParameter(f"未找到钱包: {target}")
            display_addr = w["address"]
        password = getpass.getpass("钱包密码: ")
        wallet_id = int(target.strip()) if target and target.strip().isdigit() else None
        address_arg = target.strip() if target and not target.strip().isdigit() else None
        try:
            pk = decrypt_wallet(password, wallet_id=wallet_id, address=address_arg)
        except ValueError as e:
            raise typer.BadParameter(str(e))
        console.print("[cyan]地址:[/cyan]", display_addr)
        console.print("[yellow]私钥（请妥善保管，勿泄露）:[/yellow]")
        console.print(pk)

