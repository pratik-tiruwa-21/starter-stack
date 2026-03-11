# ⚙️ starter-stack - Secure AI Agent Workspace

[![Download starter-stack](https://img.shields.io/badge/Download-Get%20Starter-blue?style=for-the-badge)](https://github.com/pratik-tiruwa-21/starter-stack)

---

starter-stack is a ready-to-use workspace built for creating and running AI agents safely. It uses a 6-layer security approach with tools that check code, sign it, run it in a safe box, and monitor its behavior. Even if you are new to AI tools or programming, this guide will help you set up starter-stack on your Windows computer.

---

## 🔍 What is starter-stack?

This workspace helps you build AI agents while keeping security in focus. AI agents are programs that can learn, make decisions, or automate tasks. This setup includes tools that work together to keep these agents safe and controlled. It uses scanning, signing, sandboxing, and observability to stop bad code or unwanted actions.

You get a cloneable setup, which means you can copy the whole environment easily. It fits developers who want to build on open-source AI safely or try new AI projects without risking their computer or data.

---

## 💻 System Requirements

To run starter-stack on your Windows PC, you need:

- Windows 10 or later (64-bit recommended)
- At least 8 GB of RAM (16 GB or more for better performance)
- 10 GB free hard drive space
- Internet connection (initial download and updates)
- Admin rights to install software

You also need to have Docker Desktop installed and running. Docker helps package all tools with their settings, so you don't have to install each separately.

---

## 🚀 Getting Started with starter-stack

This section explains how to get starter-stack working on Windows step by step.

### Step 1: Access the starter-stack page

To get the files, visit the official starter-stack page:

[![Download starter-stack](https://img.shields.io/badge/Download-Get%20Starter-green?style=for-the-badge)](https://github.com/pratik-tiruwa-21/starter-stack)

Click the link above to open the starter-stack GitHub page. This is where all files and instructions live.

### Step 2: Download the starter-stack package

On the GitHub page, look for the green **Code** button near the top right section. Click it and select **Download ZIP**. This will download the full project in a compressed folder.

Alternatively, if you prefer using tools like Git, you can clone the project using the URL shown under the **Code** button. But downloading the ZIP is simpler for non-technical users.

### Step 3: Unpack the downloaded file

Once the download finishes, open the ZIP file and extract its contents to a folder on your PC, for example, `C:\Users\YourName\Documents\starter-stack`.

### Step 4: Install Docker Desktop

starter-stack runs inside Docker containers. If you do not have Docker Desktop installed:

- Visit https://www.docker.com/get-started.
- Download the Docker Desktop installer for Windows.
- Run the installer and follow the instructions to set it up.
- Restart your computer if prompted.
- Open Docker Desktop after reboot to make sure it runs.

Once Docker Desktop is running, you are ready for the next step.

### Step 5: Open the workspace and start starter-stack

Now, open File Explorer and navigate to the folder where you extracted starter-stack.

Look for a file named `README.md` inside. This file has detailed setup information for developers but you can skip it for now.

Open the folder inside a Windows Terminal or Command Prompt:

- Hold Shift and right-click in the folder window.
- Select **Open PowerShell window here** or **Open Command window here**.

In the terminal, type:

```
docker-compose up
```

This command tells Docker to start the entire workspace, including the security layers and AI agents.

Wait while docker downloads required tools and prepares the setup. The first time can take a few minutes.

### Step 6: Access the starter-stack interface

When Docker finishes setting up, starter-stack services run in the background. You can access its control panel by opening your web browser and going to:

```
http://localhost:8080
```

This opens the main interface to work with AI agents safely. The interface lets you monitor, manage, and review agent activity.

---

## 🔧 How starter-stack protects your AI agents

starter-stack implements six layers of security:

- **Scanner**: Checks agent code and blocks unsafe commands before running.
- **Signing**: Confirms code is authorized and unmodified.
- **Sandbox**: Runs agents in a secure, restricted environment to prevent system damage.
- **Observability**: Logs agent actions and performance for review.
- **Policy Enforcement**: Uses rules to control what agents may or may not do.
- **Context Awareness**: Limits the information agents can access based on their role.

This stack prevents prompt injections, unwanted data leaks, or resource misuse while letting you safely explore AI agent development.

---

## ⚙️ Using starter-stack features

### AI Agents

You can build or test AI agents using the workspace. It supports tools compatible with Claude and OpenAI technologies.

### Code Security

Every new AI agent code you add goes through the scanner and signing steps automatically.

### Monitoring

The Grafana and Prometheus tools collect and show metrics about agent health and activity. You access all dashboards from the web interface.

### Development Tools

starter-stack supports Visual Studio Code through devcontainers. You can open the workspace in VS Code for editing or debugging if you have it installed.

### Containerization

Docker runs all components isolated from each other and from your main system. It makes setup repeatable and clean.

---

## 📥 Download and Install Again

To refresh or set up starter-stack on a new PC:

1. Visit the starter-stack GitHub page:  
   https://github.com/pratik-tiruwa-21/starter-stack

2. Download the ZIP file or clone the repository.

3. Extract the files to a chosen folder.

4. Install Docker Desktop on Windows if missing.

5. Open PowerShell inside the extracted folder.

6. Run this command:  
   `docker-compose up`

7. Wait for startup and open your browser at `http://localhost:8080`.

---

## 📚 Additional Information

- If you see errors in Docker, ensure virtualization is enabled in your BIOS.
- Ports 8080 (web interface) and 9090 (metrics) need to be free.
- You can stop starter-stack anytime by pressing `Ctrl + C` in the terminal.
- To update, download the latest ZIP and repeat the setup.
- starter-stack supports AI security topics like prompt injection protection and policy management.

---

## 🔗 Useful Links

- Starter-stack repository: https://github.com/pratik-tiruwa-21/starter-stack
- Docker Desktop for Windows: https://www.docker.com/get-started
- Visual Studio Code: https://code.visualstudio.com

---

## 🛠 Technical Support

If you run into setup issues:

- Check Docker is running properly.
- Confirm your internet connection during setup.
- Review terminal output for errors.
- Search issues page on the GitHub repository.
- Reach out to community forums for help with Docker or AI agents.