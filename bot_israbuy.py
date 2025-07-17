import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import os
from dotenv import load_dotenv

# --- CONFIGURAÇÕES ---
load_dotenv()
BOT_TOKEN = os.getenv('DISCORD_TOKEN')
GUILD_ID = 897650833888534588
LOG_CHANNEL_ID = 1382340441579720846
SALARY_CHANNEL_ID = 1385371013226827986
LOG_BOT_ID = 1379083772766720000
COMISSAO_POR_VENDA_ROBUX = 10
COMISSAO_POR_VENDA_BRL = 0.34
META_VENDAS = 2942
META_BRL = 1000.00
DB_PATH = '/data/sales_data.db'

def setup_database():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL UNIQUE,
            attendant_id INTEGER NOT NULL,
            attendant_name TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def find_member_by_name(guild, name_to_find):
    name_lower = name_to_find.lower()
    member = discord.utils.find(lambda m: m.display_name.lower() == name_lower, guild.members)
    if member: return member
    member = discord.utils.find(lambda m: m.name.lower() == name_lower, guild.members)
    return member

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
        
        # 1. Tenta ATUALIZAR a venda
        cursor.execute(
            "UPDATE sales SET attendant_id = ?, attendant_name = ? WHERE message_id = ?",
            (self.new_attendant.id, self.new_attendant.display_name, self.original_message_id)
        )
        rows_affected = cursor.rowcount
        
        # 2. Se não encontrou (0 linhas afetadas), CRIA a venda
        if rows_affected == 0:
            cursor.execute(
                "INSERT OR IGNORE INTO sales (message_id, attendant_id, attendant_name) VALUES (?, ?, ?)",
                (self.original_message_id, self.new_attendant.id, self.new_attendant.display_name)
            )
        
        conn.commit()
        conn.close()
        
        await interaction.response.send_message(f"✅ Venda registrada/corrigida para {self.new_attendant.mention}!", ephemeral=True)
        
        # Atualiza a contagem total de vendas no canal
        await self.bot.update_total_sales_message()

        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Não, Cancelar", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ Correção cancelada.", ephemeral=True)
        for item in self.children:
            item.disabled = True
        await interaction.message.edit(view=self)

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

        # Lógica de Nova Venda
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

        # Lógica de Correção
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
                    if old_message.author.id == LOG_BOT_ID and old_message.embeds:
                        if old_message.embeds[0].title and "Log de Compra" in old_message.embeds[0].title:
                            message_to_correct = old_message; break
            if message_to_correct:
                # Passa a instância do bot (self) para a View
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

bot = IsraBuyBot()
@bot.tree.command(name="salario", description="Calcula o salário de um atendente com base nas vendas.")
@app_commands.describe(membro="O membro para calcular o salário.")
async def salario(interaction: discord.Interaction, membro: discord.Member):
    await interaction.response.defer(ephemeral=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM sales WHERE attendant_id = ?", (membro.id,))
    sales_count = cursor.fetchone()[0]
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

if __name__ == "__main__":
    setup_database()
    if BOT_TOKEN:
        bot.run(BOT_TOKEN)
    else:
        print("ERRO CRÍTICO: O token do bot (DISCORD_TOKEN) não foi encontrado.")
