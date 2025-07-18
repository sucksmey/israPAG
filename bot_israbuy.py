import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
from dotenv import load_dotenv
import datetime

# --- CONFIGURAÇÕES GERAIS ---
load_dotenv()
BOT_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = 897650833888534588
DB_PATH = '/data/sales_data.db'

# --- CONFIGURAÇÕES DE SALÁRIO DE ATENDENTES ---
LOG_CHANNEL_ID = 1382340441579720846
SALARY_CHANNEL_ID = 1385371013226827986
LOG_BOT_ID = 1379083772766720000
COMISSAO_POR_VENDA_ROBUX = 10
COMISSAO_POR_VENDA_BRL = 0.34
META_VENDAS = 2942
META_BRL = 1000.00

# --- CONFIGURAÇÕES DE FIDELIDADE (COM IDs CORRIGIDOS) ---
ADMIN_VENDAS_ROLE_ID = 1379126175317622965
LOYALTY_NOTIFICATION_CHANNEL_ID = 1380180609653018735

# IDs dos cargos de fidelidade
LOYALTY_ROLE_10_ID = 1394109025246773340
LOYALTY_ROLE_50_ID = 1394109339316392047
LOYALTY_ROLE_100_ID = 1394109339316392047 # Assumindo o mesmo ID de 50, conforme informado.

LOYALTY_TIERS = {
    10: {"name": "Cliente Fiel 🥉", "reward": "1.000 Robux por R$35 na sua próxima compra!", "role_id": LOYALTY_ROLE_10_ID, "emoji": "🥉"},
    20: {"name": "Cliente Bronze II", "reward": "100 Robux grátis na sua próxima compra!", "role_id": None, "emoji": "🎯"},
    30: {"name": "Cliente Prata 🥈", "reward": "Desconto vitalício de R$1 em pacotes acima de 500 Robux!", "role_id": None, "emoji": "🥈"},
    40: {"name": "Cliente Prata II", "reward": "300 Robux grátis na sua próxima compra!", "role_id": None, "emoji": "🎯"},
    50: {"name": "Cliente Ouro 🥇", "reward": "Um pacote de 1.000 Robux por R$30 (uso único)!", "role_id": LOYALTY_ROLE_50_ID, "emoji": "🥇"},
    60: {"name": "Cliente Diamante 💎", "reward": "Acesso ao 'Clube VIP Fidelidade' (entregas prioritárias, mimos mensais e cargo especial)!", "role_id": None, "emoji": "👑"},
    70: {"name": "Cliente Mestre 🔥", "reward": "Combo especial: 500 + 300 Robux por apenas R$25!", "role_id": None, "emoji": "🔥"},
    100: {"name": "Lenda da Israbuy 🏆", "reward": "Mural dos Deuses, 1.000 Robux grátis e acesso permanente a promoções VIP!", "role_id": LOYALTY_ROLE_100_ID, "emoji": "🏆"}
}

# --- BANCO DE DADOS ---
def setup_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT, message_id INTEGER NOT NULL UNIQUE,
            attendant_id INTEGER NOT NULL, attendant_name TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS loyalty_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
            admin_id INTEGER NOT NULL, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

# --- FUNÇÕES AUXILIARES ---
def find_member_by_name(guild, name_to_find):
    name_lower = name_to_find.lower()
    member = discord.utils.find(lambda m: m.display_name.lower() == name_lower, guild.members)
    if member: return member
    member = discord.utils.find(lambda m: m.name.lower() == name_lower, guild.members)
    return member

# --- CLASSE DA VIEW DE CORREÇÃO (SISTEMA DE SALÁRIO) ---
class CorrectionView(discord.ui.View):
    def __init__(self, bot_instance, original_message_id, new_attendant):
        super().__init__(timeout=300)
        self.bot = bot_instance
        self.original_message_id = original_message_id
        self.new_attendant = new_attendant

    @discord.ui.button(label="Sim, Corrigir", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("UPDATE sales SET attendant_id = ?, attendant_name = ? WHERE message_id = ?",(self.new_attendant.id, self.new_attendant.display_name, self.original_message_id))
        if cursor.rowcount == 0:
            cursor.execute("INSERT OR IGNORE INTO sales (message_id, attendant_id, attendant_name) VALUES (?, ?, ?)",(self.original_message_id, self.new_attendant.id, self.new_attendant.display_name))
        conn.commit()
        conn.close()
        await interaction.response.send_message(f"✅ Venda registrada/corrigida para {self.new_attendant.mention}!", ephemeral=True)
        await self.bot.update_total_sales_message()
        for item in self.children: item.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Não, Cancelar", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ Correção cancelada.", ephemeral=True)
        for item in self.children: item.disabled = True
        await interaction.message.edit(view=self)

# --- CLASSE PRINCIPAL DO BOT ---
class IsraBuyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True; intents.messages = True
        intents.message_content = True; intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print(f"Comandos slash sincronizados para o servidor {GUILD_ID}.")

    async def on_ready(self):
        print(f'Bot conectado como {self.user}')

    async def on_message(self, message: discord.Message):
        if message.author.id == self.user.id: return
        if message.author.bot and message.author.id != LOG_BOT_ID: return

        # --- Lógica de Salário de Atendente ---
        if message.channel.id == LOG_CHANNEL_ID and message.author.id == LOG_BOT_ID and message.embeds:
            embed = message.embeds[0]
            if embed.title and "Log de Compra" in embed.title:
                attendant_name_str = next((f.value.strip().replace('@', '') for f in embed.fields if f.name=="Atendente"),None)
                if attendant_name_str:
                    guild = self.get_guild(GUILD_ID)
                    attendant_member = find_member_by_name(guild, attendant_name_str)
                    attendant_id = attendant_member.id if attendant_member else 0
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute("INSERT OR IGNORE INTO sales (message_id, attendant_id, attendant_name) VALUES (?, ?, ?)",(message.id, attendant_id, attendant_name_str))
                    conn.commit()
                    if attendant_id != 0 and cursor.rowcount > 0:
                        cursor.execute("SELECT COUNT(*) FROM sales WHERE attendant_id = ?", (attendant_id,))
                        sales_count = cursor.fetchone()[0]
                        if sales_count > 0 and sales_count % 10 == 0:
                            salary_channel = self.get_channel(SALARY_CHANNEL_ID)
                            if salary_channel:
                                await salary_channel.send(f"🎉 Parabéns {attendant_member.mention}! Você alcançou **{sales_count}** vendas!")
                    conn.close()
                    await self.update_total_sales_message()

        if message.channel.id == LOG_CHANNEL_ID and "atendente" in message.content.lower() and message.mentions:
            target_mention = discord.utils.find(lambda m: not m.bot, message.mentions)
            if not target_mention: return
            corrected_attendant = target_mention
            message_to_correct = None
            if message.reference and message.reference.message_id:
                try: message_to_correct = await message.channel.fetch_message(message.reference.message_id)
                except discord.NotFound: message_to_correct = None
            if not message_to_correct:
                async for old_message in message.channel.history(limit=10, before=message):
                    if old_message.author.id == LOG_BOT_ID and old_message.embeds and old_message.embeds[0].title and "Log de Compra" in old_message.embeds[0].title:
                        message_to_correct = old_message; break
            if message_to_correct:
                view = CorrectionView(self, message_to_correct.id, corrected_attendant)
                await message.reply(f"Você deseja corrigir/registrar a venda (`{message_to_correct.id}`) para {corrected_attendant.mention}?", view=view)

    async def update_total_sales_message(self):
        salary_channel = self.get_channel(SALARY_CHANNEL_ID)
        if not salary_channel: return
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM sales")
        total_sales = cursor.fetchone()[0]
        conn.close()
        message_to_edit = None
        async for msg in salary_channel.history(limit=100):
            if msg.author == self.user and "Total de Vendas Registradas" in msg.content:
                message_to_edit = msg; break
        content = f"📊 **Total de Vendas Registradas:** {total_sales}"
        if message_to_edit: await message_to_edit.edit(content=content)
        else: await salary_channel.send(content)

    # --- Lógica de Fidelidade de Clientes ---
    async def check_loyalty_milestones(self, interaction: discord.Interaction, customer: discord.Member):
        try:
            guild = interaction.guild
            notification_channel = guild.get_channel(LOYALTY_NOTIFICATION_CHANNEL_ID)
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM loyalty_purchases WHERE customer_id = ?", (customer.id,))
            purchase_count = cursor.fetchone()[0] or 0
            conn.close()
            log_message, dm_message = "", ""

            if purchase_count == 1:
                dm_message = f"Olá, {customer.display_name}! Boas-vindas ao nosso Programa de Fidelidade! Agradecemos muito pela sua primeira compra. A cada nova compra, você fica mais perto de ganhar prêmios incríveis. Use **/beneficiosfidelidade** para ver as recompensas!"
                log_message = f"✅ DM de boas-vindas à fidelidade enviada para {customer.mention}."

            elif purchase_count in LOYALTY_TIERS:
                tier_data = LOYALTY_TIERS[purchase_count]
                if notification_channel:
                    notif_embed = discord.Embed(title="🎉 Meta de Fidelidade Atingida! 🎉", description=f"O cliente {customer.mention} atingiu a marca de **{purchase_count} compras**!", color=discord.Color.green())
                    notif_embed.add_field(name="Recompensa Desbloqueada", value=f"**{tier_data['name']}**: {tier_data['reward']}")
                    notif_embed.set_thumbnail(url=customer.display_avatar.url)
                    await notification_channel.send(embed=notif_embed)
                dm_message = f"Parabéns, {customer.display_name}! 🥳 Você atingiu a marca de **{purchase_count} compras** e desbloqueou uma recompensa incrível: **{tier_data['reward']}** Continue assim!"
                log_message = f"✅ DM de meta de {purchase_count} compras enviada para {customer.mention}."
                if tier_data['role_id']:
                    role_to_add = guild.get_role(tier_data['role_id'])
                    if role_to_add: await customer.add_roles(role_to_add, reason=f"Atingiu {purchase_count} compras.")
            else:
                return

            if dm_message:
                try:
                    await customer.send(dm_message)
                    if notification_channel and log_message: await notification_channel.send(log_message, delete_after=3600)
                except discord.Forbidden:
                    if notification_channel and log_message: await notification_channel.send(f"❌ Falha ao enviar DM de fidelidade para {customer.mention} (provavelmente DMs fechadas).", delete_after=3600)
        except Exception as e:
            print(f"Erro ao verificar milestones de fidelidade para {customer.name}: {e}")

bot = IsraBuyBot()

# --- COMANDOS ---

@bot.tree.command(name="salario", description="Calcula o salário de um atendente com base nas vendas.")
@app_commands.describe(membro="O membro para calcular o salário.")
async def salario(interaction: discord.Interaction, membro: discord.Member):
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sales WHERE attendant_id = ?", (membro.id,))
    sales_count = cursor.fetchone()[0] or 0
    conn.close()
    salary_robux = sales_count * COMISSAO_POR_VENDA_ROBUX
    salary_brl = sales_count * COMISSAO_POR_VENDA_BRL
    progresso_meta = (salary_brl / META_BRL) * 100 if META_BRL > 0 else 0
    embed = discord.Embed(title=f"💰 Salário de {membro.display_name}", color=discord.Color.gold())
    embed.set_thumbnail(url=membro.display_avatar.url)
    embed.add_field(name="Vendas Realizadas", value=f"`{sales_count}`", inline=True)
    embed.add_field(name="Comissão (Robux)", value=f"`R$ {salary_robux}`", inline=True)
    embed.add_field(name="Comissão (BRL)", value=f"`R$ {salary_brl:.2f}`", inline=True)
    embed.add_field(name=f"🎯 Meta de Comissão (R$ {META_BRL:.2f})", value=f"Progresso: **{progresso_meta:.2f}%** (`{sales_count}` de `{META_VENDAS}` vendas)", inline=False)
    embed.set_footer(text=f"ID do Atendente: {membro.id}")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="beneficiosfidelidade", description="Mostra seus benefícios ou de outro membro (requer admin).")
@app_commands.describe(membro="Opcional: O membro que você quer consultar.")
async def beneficiosfidelidade(interaction: discord.Interaction, membro: discord.Member = None):
    target_user = membro or interaction.user
    is_self_check = not membro

    if not is_self_check and not interaction.user.guild_permissions.administrator:
        admin_role = interaction.guild.get_role(ADMIN_VENDAS_ROLE_ID)
        if not (admin_role and admin_role in interaction.user.roles):
            return await interaction.response.send_message("❌ Você não tem permissão para ver os benefícios de outros membros.", ephemeral=True)

    await interaction.response.defer(ephemeral=not is_self_check)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM loyalty_purchases WHERE customer_id = ?", (target_user.id,))
    purchase_count = cursor.fetchone()[0] or 0
    conn.close()

    embed = discord.Embed(title=f"🌟 Programa de Fidelidade de {target_user.display_name}",
        description=f"Quanto mais compras, mais benefícios exclusivos são desbloqueados.\n\n**{target_user.display_name} tem atualmente `{purchase_count}` compras verificadas.**",
        color=discord.Color.gold())
    embed.set_thumbnail(url=target_user.display_avatar.url)

    for count, data in LOYALTY_TIERS.items():
        status_emoji = "✅" if purchase_count >= count else "❌"
        embed.add_field(name=f"{status_emoji} {count} Compras: {data['name']}", value=data['reward'], inline=False)
    
    embed.set_footer(text="As recompensas são aplicadas automaticamente ao atingir a meta.")
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="adicionarfidelidade", description="[Admin] Adiciona uma compra de fidelidade para um cliente.")
@app_commands.describe(cliente="O cliente que realizou a compra.")
@app_commands.checks.has_role(ADMIN_VENDAS_ROLE_ID)
async def adicionarfidelidade(interaction: discord.Interaction, cliente: discord.Member):
    await interaction.response.defer(ephemeral=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO loyalty_purchases (customer_id, admin_id) VALUES (?, ?)", (cliente.id, interaction.user.id))
    conn.commit()
    conn.close()

    await interaction.followup.send(f"✅ Compra de fidelidade registrada para {cliente.mention}!", ephemeral=True)
    
    notification_channel = interaction.guild.get_channel(LOYALTY_NOTIFICATION_CHANNEL_ID)
    if notification_channel:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM loyalty_purchases WHERE customer_id = ?", (cliente.id,))
        purchase_count = cursor.fetchone()[0] or 0
        conn.close()
        
        public_embed = discord.Embed(title=f"🌟 Programa de Fidelidade de {cliente.display_name}",
                                     description=f"**{cliente.display_name} tem atualmente `{purchase_count}` compras verificadas.**",
                                     color=discord.Color.gold())
        public_embed.set_thumbnail(url=cliente.display_avatar.url)
        for count, data in LOYALTY_TIERS.items():
            status_emoji = "✅" if purchase_count >= count else "❌"
            public_embed.add_field(name=f"{status_emoji} {count} Compras: {data['name']}", value=data['reward'], inline=False)
        
        await notification_channel.send(embed=public_embed, delete_after=3600)

    await bot.check_loyalty_milestones(interaction, cliente)

@adicionarfidelidade.error
async def adicionarfidelidade_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingRole):
        await interaction.response.send_message("❌ Você não tem permissão para usar este comando.", ephemeral=True)
    else:
        await interaction.response.send_message(f"😕 Ocorreu um erro: {error}", ephemeral=True)
        raise error

if __name__ == "__main__":
    setup_database()
    bot.run(BOT_TOKEN)
