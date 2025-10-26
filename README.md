# 🏥 Analisador de Laudo AIH - Versão 2.0

Aplicação Streamlit para extração e análise automática de dados de laudos AIH (Autorização de Internação Hospitalar) a partir de PDFs e imagens.

## ✨ Novidades da Versão 2.0

- 🔍 **Pós-processamento inteligente de OCR**: Corrige palavras coladas e caracteres mal interpretados
- ✅ **Validação automática**: CPF, CNS e CEP são validados automaticamente
- 📊 **Formatação automática**: Números formatados nos padrões corretos
- 🎯 **Alertas visuais**: Ícones indicam campos válidos ou com problemas
- 📋 **Exportação de dados**: JSON e TXT com um clique
- 🔄 **Melhor legibilidade**: Texto de debug formatado e organizado

## 🚀 Como Usar

### Instalação

```bash
pip install -r requirements.txt
```

### Executar

```bash
streamlit run app.py
```

### Processar um Documento

1. Clique em "Carregar Laudo (PDF ou Imagem)"
2. Selecione um arquivo PDF ou imagem (PNG, JPG, JPEG)
3. Aguarde a análise automática
4. Revise os dados extraídos no formulário
5. Observe os ícones de validação (✅ ou ⚠️)
6. Use os botões para exportar ou limpar

## 📋 Funcionalidades

### Extração de Dados
- Nome do paciente
- Nome da mãe
- CPF (com validação)
- Cartão SUS/CNS (com validação)
- Data de nascimento
- Sexo e raça/cor
- Prontuário
- Endereço completo
- Município e UF
- CEP (com validação)
- Telefone (com formatação)
- Diagnóstico

### Validações Automáticas
- ✅ CPF: Algoritmo de dígito verificador
- ✅ CNS: Validação do Cartão Nacional de Saúde
- ✅ CEP: Formato correto (8 dígitos)

### Formatação Automática
- CPF: `XXX.XXX.XXX-XX`
- CEP: `XXXXX-XXX`
- Telefone: `(XX) XXXXX-XXXX` ou `(XX) XXXX-XXXX`

## 🔧 Tecnologias

- **Streamlit**: Interface web
- **PyMuPDF**: Extração de texto de PDF
- **RapidOCR**: OCR para imagens
- **OpenCV**: Pré-processamento de imagens
- **Pillow**: Manipulação de imagens
- **NumPy**: Operações numéricas

## 📖 Documentação Completa

Veja [MELHORIAS.md](MELHORIAS.md) para detalhes técnicos sobre todas as melhorias implementadas.

## 🛡️ Segurança e Privacidade

- Todos os dados são processados localmente
- Nenhuma informação é enviada para servidores externos
- Os arquivos carregados não são armazenados permanentemente

## 📝 Licença

Este projeto é de uso interno para fins médicos e hospitalares.

