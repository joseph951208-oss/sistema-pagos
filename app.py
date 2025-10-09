from flask import Flask, render_template, request, redirect, url_for, session, flash
from openpyxl import load_workbook
from werkzeug.security import check_password_hash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
import os
import pandas as pd
import json


app = Flask(__name__)
app.secret_key = 'clave-secreta-muy-segura'

# URL de PostgreSQL 
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://sistema_pagos_ohrc_user:LsMj7GgLXTPIW2C7rTvE3kfCjQz4j9OW@dpg-d3jh63t6ubrc73cr05ag-a.oregon-postgres.render.com/sistema_pagos_ohrc")

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    role = db.Column(db.String(20), nullable=False)

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cedula = db.Column(db.String(20), unique=True, nullable=False)
    nombres = db.Column(db.String(100), nullable=False)
    direccion = db.Column(db.String(200))
    fecha_instalacion = db.Column(db.Date)
    pagos = db.relationship('Pago', backref='cliente', lazy=True)

class Pago(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    documento = db.Column(db.String(50))
    fecha_pago = db.Column(db.Date, nullable=False)
    forma_pago = db.Column(db.String(50), nullable=False)

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER


# -------------------- Rutas --------------------
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = USERS.get(username)

        # Verificar hash de contraseña
        if user and check_password_hash(user['password_hash'], password):
            session['username'] = username
            session['role'] = user['role']

            if user['role'] == 'admin':
                return redirect(url_for('upload_files'))
            elif user['role'] == 'reader':
                return redirect(url_for('consulta'))

        flash("Usuario o contraseña incorrectos", "danger")

    return render_template('login.html')

# Subir archivos (solo admin)
@app.route('/subir', methods=['GET', 'POST'])
def upload_files():
    if 'username' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        clientes_file = request.files.get('clientes')
        movimientos_file = request.files.get('movimientos')

        # Contador de archivos subidos
        archivos_subidos = 0

        if clientes_file and clientes_file.filename != '':
            clientes_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'clientes.xlsx'))
            archivos_subidos += 1

        if movimientos_file and movimientos_file.filename != '':
            movimientos_file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'movimientos.xlsx'))
            archivos_subidos += 1

        if archivos_subidos == 0:
            flash('Debes subir al menos un archivo.', 'danger')
        else:
            flash(f'{archivos_subidos} archivo(s) cargado(s) exitosamente ✅', 'success')

        return redirect(url_for('upload_files'))

    return render_template('index.html')

# Registro de pagos
@app.route('/registro_pago', methods=['GET', 'POST'])
def registro_pago():
    if 'username' not in session:
        return redirect(url_for('login'))

    # ---------------- Cargar clientes ----------------
    clientes_path = os.path.join(app.config['UPLOAD_FOLDER'], 'clientes.xlsx')
    clientes = []
    if os.path.exists(clientes_path):
        df_clientes = pd.read_excel(clientes_path, header=0)
        df_clientes.columns = df_clientes.columns.str.strip().str.upper()
        nombre_col = [col for col in df_clientes.columns if 'NOMBRE' in col]
        if nombre_col:
            clientes = df_clientes[nombre_col[0]].dropna().astype(str).tolist()

    # ---------------- Cargar documentos de movimientos ----------------
    movimientos_path = os.path.join(app.config['UPLOAD_FOLDER'], 'movimientos.xlsx')
    documentos = []
    if os.path.exists(movimientos_path):
        wb = load_workbook(movimientos_path)
        ws = wb.active
        header_row = 6  # fila donde está "Nro. Documento"
        col_idx = None

        # Buscar la columna de "Nro. Documento"
        for cell in ws[header_row]:
            if cell.value and str(cell.value).strip().upper() == "NRO. DOCUMENTO":
                col_idx = cell.column
                break

        if col_idx:
            merged_ranges = ws.merged_cells.ranges
            for row in range(header_row + 1, ws.max_row + 1):
                cell = ws.cell(row=row, column=col_idx)
                val = cell.value
                if not val:
                    for mrange in merged_ranges:
                        if cell.coordinate in mrange:
                            val = ws.cell(mrange.min_row, mrange.min_col).value
                            break
                if val and str(val).strip() not in ["", "NaN", "nan"]:
                    documentos.append(str(val).strip())

    # ---------------- POST: Registrar pago ----------------
    if request.method == 'POST':
        cliente = request.form.get('cliente')
        doc_num = request.form.get('doc_num')
        fecha_pago = request.form.get('fecha_pago')
        forma_pago = request.form.get('forma_pago')

        # Validar cliente
        if cliente not in clientes:
            flash("Cliente no válido.", "danger")
            return redirect(url_for('registro_pago'))

        # Validar documento si es transferencia
        if forma_pago.upper() == "TRANSFERENCIA":
            if not doc_num:
                flash("Debe ingresar número de documento para transferencia.", "danger")
                return redirect(url_for('registro_pago'))
            if doc_num not in documentos:
                flash(f"Número de documento {doc_num} no encontrado en movimientos.", "danger")
                return redirect(url_for('registro_pago'))

        # ---------------- Validar pagos existentes ----------------
        pagos_path = os.path.join(app.config['UPLOAD_FOLDER'], 'pagos.xlsx')
        if os.path.exists(pagos_path):
            df_pagos = pd.read_excel(pagos_path)
        else:
            df_pagos = pd.DataFrame(columns=['Cliente', 'Documento', 'Fecha', 'Forma'])

        # Normalizar documentos existentes
        df_pagos['Documento'] = df_pagos['Documento'].astype(str).str.strip()
        pagos_documentos = set(df_pagos['Documento'].dropna().tolist())

        # Normalizar doc_num ingresado
        doc_num = (doc_num or "").strip()

        # Validar duplicados
        if doc_num and doc_num in pagos_documentos:
            flash(f"El documento {doc_num} ya fue registrado.", "warning")
            return redirect(url_for('registro_pago'))

        # ---------------- Guardar pago ----------------
        df_pagos = pd.concat([df_pagos, pd.DataFrame([{
            'Cliente': cliente,
            'Documento': doc_num if doc_num else '',
            'Fecha': fecha_pago,
            'Forma': forma_pago
        }])], ignore_index=True)

        df_pagos.to_excel(pagos_path, index=False)
        flash("Pago registrado con éxito ✅", "success")
        return redirect(url_for('registro_pago'))

    # ---------------- Render ----------------
    return render_template('registro_pago.html', clientes=clientes, documentos=documentos)

# Consulta (lector y admin)
@app.route('/consulta')
def consulta():
    if 'username' not in session:
        return redirect(url_for('login'))

    # Cargar clientes
    clientes_path = os.path.join(app.config['UPLOAD_FOLDER'], 'clientes.xlsx')
    clientes = []
    if os.path.exists(clientes_path):
        df_clientes = pd.read_excel(clientes_path, header=0)
        df_clientes.columns = df_clientes.columns.str.strip().str.upper()
        nombre_col = [col for col in df_clientes.columns if 'NOMBRE' in col]
        if nombre_col:
            clientes = df_clientes[nombre_col[0]].tolist()

    # Cargar pagos
    pagos_path = os.path.join(app.config['UPLOAD_FOLDER'], 'pagos.xlsx')
    pagos = []
    if os.path.exists(pagos_path):
        df_pagos = pd.read_excel(pagos_path)
        cliente_filtro = request.args.get('cliente')
        doc_filtro = request.args.get('documento')
        if cliente_filtro:
            df_pagos = df_pagos[df_pagos['Cliente'] == cliente_filtro]
        if doc_filtro:
            df_pagos = df_pagos[df_pagos['Documento'].astype(str).str.contains(doc_filtro)]
        pagos = df_pagos.to_dict(orient='records')

    return render_template('consulta.html', clientes=clientes, pagos=pagos)

@app.route('/registro_cliente', methods=['GET', 'POST'])
def registro_cliente():
    if 'username' not in session:
        return redirect(url_for('login'))

    clientes_path = os.path.join(app.config['UPLOAD_FOLDER'], 'clientes.xlsx')

    # Crear el archivo si no existe
    if not os.path.exists(clientes_path):
        df_clientes = pd.DataFrame(columns=['ID', 'NOMBRE', 'IP'])
        df_clientes.to_excel(clientes_path, index=False)

    df_clientes = pd.read_excel(clientes_path)

    # Asegurar columnas
    df_clientes.columns = df_clientes.columns.str.strip().str.upper()
    if 'ID' not in df_clientes.columns:
        df_clientes['ID'] = range(1, len(df_clientes) + 1)
    if 'NOMBRE' not in df_clientes.columns:
        df_clientes['NOMBRE'] = ""
    if 'IP' not in df_clientes.columns:
        df_clientes['IP'] = ""

    mensaje = None

    if request.method == 'POST':
        accion = request.form.get('accion')

        if accion == "agregar":
            nombre = request.form.get('nombre').strip()
            ip = request.form.get('ip').strip()

            if nombre and ip:
                nuevo_id = df_clientes['ID'].max() + 1 if not df_clientes.empty else 1
                df_clientes = pd.concat([df_clientes, pd.DataFrame([{
                    'ID': nuevo_id,
                    'NOMBRE': nombre,
                    'IP': ip
                }])], ignore_index=True)

                df_clientes.to_excel(clientes_path, index=False)
                mensaje = "Cliente agregado con éxito ✅"

        elif accion == "modificar":
            cliente_id = int(request.form.get('id'))
            nuevo_nombre = request.form.get('nombre').strip()
            nueva_ip = request.form.get('ip').strip()

            if cliente_id in df_clientes['ID'].values:
                df_clientes.loc[df_clientes['ID'] == cliente_id, 'NOMBRE'] = nuevo_nombre
                df_clientes.loc[df_clientes['ID'] == cliente_id, 'IP'] = nueva_ip
                df_clientes.to_excel(clientes_path, index=False)
                mensaje = f"Cliente {cliente_id} modificado ✅"

        elif accion == "buscar":
            termino = request.form.get('buscar').strip().lower()
            df_clientes = df_clientes[
                df_clientes['NOMBRE'].str.lower().str.contains(termino) |
                df_clientes['IP'].str.lower().str.contains(termino)
            ]

        elif accion == "eliminar":
            cliente_id = int(request.form.get('id'))
            if cliente_id in df_clientes['ID'].values:
                df_clientes = df_clientes[df_clientes['ID'] != cliente_id]
                df_clientes.to_excel(clientes_path, index=False)
                mensaje = f"Cliente {cliente_id} eliminado ❌"

    clientes = df_clientes.to_dict(orient='records')
    return render_template('registro_cliente.html', clientes=clientes, mensaje=mensaje)

@app.context_processor
def inject_user_role():
    return dict(user_role=session.get('role'))

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# -------------------- Run --------------------
if __name__ == '__main__':
    app.run(debug=True)
