import streamlit as st
import pymongo
import hashlib
import datetime
import time
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import random
import string
import threading
import uuid
import os
from dotenv import load_dotenv

# Load environment variables (optional)
load_dotenv()

# =====================
# DATABASE CONFIGURATION
# =====================

# MongoDB connection setup
def get_database_connection():
    mongo_uri = os.getenv("MONGO_URI","mongodb+srv://ajaykarthik:1234@cluster0.wqelv.mongodb.net/" )
    client = pymongo.MongoClient(mongo_uri)
    db = client["task_management_db"]
    return db

# Initialize collections
def initialize_database():
    db = get_database_connection()
    
    # Create users collection if it doesn't exist
    if "users" not in db.list_collection_names():
        users_collection = db["users"]
        # Create index for username uniqueness
        users_collection.create_index([("username", pymongo.ASCENDING)], unique=True)
        
        # Create default admin user
        admin_password = hash_password("admin123")
        users_collection.insert_one({
            "username": "admin",
            "password": admin_password,
            "role": "admin",
            "experience_level": "Senior",
            "points": 100,
            "task_history": [],
            "created_at": datetime.now()
        })
        
        # Create default employee users
        for i in range(1, 4):
            employee_password = hash_password(f"employee{i}")
            users_collection.insert_one({
                "username": f"employee{i}",
                "password": employee_password,
                "role": "employee",
                "experience_level": "Junior",
                "points": 100,
                "task_history": [],
                "created_at": datetime.now()
            })
    
    # Create tasks collection if it doesn't exist
    if "tasks" not in db.list_collection_names():
        tasks_collection = db["tasks"]
        tasks_collection.create_index([("title", pymongo.ASCENDING)])

# =====================
# SECURITY UTILITIES
# =====================

# Password hashing function
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# User authentication function
def authenticate_user(username, password):
    db = get_database_connection()
    users = db["users"]
    
    hashed_password = hash_password(password)
    user = users.find_one({"username": username, "password": hashed_password})
    
    return user

# Session state initialization
def initialize_session_state():
    if 'logged_in' not in st.session_state:
        st.session_state.logged_in = False
    if 'current_user' not in st.session_state:
        st.session_state.current_user = None
    if 'role' not in st.session_state:
        st.session_state.role = None
    if 'task_timers' not in st.session_state:
        st.session_state.task_timers = {}

# =====================
# USER MANAGEMENT
# =====================

# Get all employees (for admin assignment)
def get_all_employees():
    db = get_database_connection()
    users = db["users"]
    employees = list(users.find({"role": "employee"}))
    return employees

# Update user experience level based on completed tasks
def update_experience_level(username):
    db = get_database_connection()
    users = db["users"]
    tasks = db["tasks"]
    
    completed_tasks = tasks.count_documents({
        "assignment_history": username,
        "status": "completed"
    })
    
    # Determine new experience level
    if completed_tasks <= 20:
        experience_level = "Junior"
    elif completed_tasks <= 50:
        experience_level = "Mid"
    else:
        experience_level = "Senior"
    
    # Update user's experience level
    users.update_one(
        {"username": username},
        {"$set": {"experience_level": experience_level}}
    )
    
    return experience_level

# Get point deduction based on experience level
def get_point_deduction(experience_level):
    if experience_level == "Junior":
        return 5
    elif experience_level == "Mid":
        return 3
    else:  # Senior
        return 2

# =====================
# TASK MANAGEMENT
# =====================

# Create a new task
def create_task(title, description, priority, assigned_to):
    db = get_database_connection()
    tasks = db["tasks"]
    
    created_at = datetime.now()
    deadline = created_at + timedelta(minutes=5)
    
    task_id = str(uuid.uuid4())
    
    task = {
        "task_id": task_id,
        "title": title,
        "description": description,
        "priority": priority,
        "created_at": created_at,
        "deadline": deadline,
        "status": "pending",
        "assigned_to": assigned_to,
        "assignment_history": [assigned_to]
    }
    
    tasks.insert_one(task)
    
    # Add task to user's history
    db["users"].update_one(
        {"username": assigned_to},
        {"$push": {"task_history": task_id}}
    )
    
    return task

# Get tasks for a specific user
def get_user_tasks(username):
    db = get_database_connection()
    tasks = db["tasks"]
    user_tasks = list(tasks.find({"assigned_to": username, "status": "pending"}))
    return user_tasks

# Get all tasks (for admin view)
def get_all_tasks(status_filter=None):
    db = get_database_connection()
    tasks = db["tasks"]
    
    query = {}
    if status_filter and status_filter != "All":
        query["status"] = status_filter.lower()
    
    all_tasks = list(tasks.find(query).sort("deadline", 1))
    return all_tasks

# Mark task as completed
def complete_task(task_id, username):
    db = get_database_connection()
    tasks = db["tasks"]
    users = db["users"]
    
    # Update task status
    tasks.update_one(
        {"task_id": task_id},
        {"$set": {"status": "completed"}}
    )
    
    # Update user's experience and points
    update_experience_level(username)
    
    # Assign bonus points for completion (2 points)
    users.update_one(
        {"username": username},
        {"$inc": {"points": 2}}
    )
    
    # Create and assign a new task to next employee
    reassign_task_to_next_employee(task_id)

# Handle expired tasks
def handle_expired_task(task_id):
    db = get_database_connection()
    tasks = db["tasks"]
    users = db["users"]
    
    # Get task information
    task = tasks.find_one({"task_id": task_id})
    
    if task and task["status"] == "pending":
        current_assignee = task["assigned_to"]
        
        # Get user's experience level and calculate point deduction
        user = users.find_one({"username": current_assignee})
        point_deduction = get_point_deduction(user["experience_level"])
        
        # Deduct points
        users.update_one(
            {"username": current_assignee},
            {"$inc": {"points": -point_deduction}}
        )
        
        # Mark task as expired
        tasks.update_one(
            {"task_id": task_id},
            {"$set": {"status": "expired"}}
        )
        
        # Reassign to next employee
        reassign_task_to_next_employee(task_id)

# Reassign task to next available employee
def reassign_task_to_next_employee(original_task_id):
    db = get_database_connection()
    tasks = db["tasks"]
    users = db["users"]
    
    # Get original task details
    original_task = tasks.find_one({"task_id": original_task_id})
    if not original_task:
        return None
    
    # Get all employees except the current assignee
    employees = list(users.find({"role": "employee", "username": {"$ne": original_task["assigned_to"]}}))
    
    if not employees:
        # Fallback if no other employees are available
        return None
    
    # Select a random employee for assignment
    next_employee = random.choice(employees)
    
    # Create a new task with the same details
    created_at = datetime.now()
    deadline = created_at + timedelta(minutes=5)
    
    new_task_id = str(uuid.uuid4())
    
    new_task = {
        "task_id": new_task_id,
        "title": original_task["title"],
        "description": original_task["description"],
        "priority": original_task.get("priority", "Medium"),
        "created_at": created_at,
        "deadline": deadline,
        "status": "pending",
        "assigned_to": next_employee["username"],
        "assignment_history": original_task["assignment_history"] + [next_employee["username"]],
        "original_task_id": original_task_id
    }
    
    tasks.insert_one(new_task)
    
    # Add task to user's history
    users.update_one(
        {"username": next_employee["username"]},
        {"$push": {"task_history": new_task_id}}
    )
    
    return new_task

# =====================
# ANALYTICS & REPORTING
# =====================

# Get employee performance metrics
def get_employee_performance():
    db = get_database_connection()
    users = db["users"]
    tasks = db["tasks"]
    
    employees = list(users.find({"role": "employee"}))
    performance_data = []
    
    for employee in employees:
        username = employee["username"]
        completed_count = tasks.count_documents({
            "assignment_history": username,
            "status": "completed"
        })
        expired_count = tasks.count_documents({
            "assignment_history": username,
            "status": "expired"
        })
        
        performance_data.append({
            "username": username,
            "experience_level": employee["experience_level"],
            "points": employee["points"],
            "completed_tasks": completed_count,
            "expired_tasks": expired_count,
            "completion_rate": completed_count / (completed_count + expired_count) * 100 if (completed_count + expired_count) > 0 else 0
        })
    
    return performance_data

# Get task distribution analytics
def get_task_distribution():
    db = get_database_connection()
    tasks = db["tasks"]
    
    pending_count = tasks.count_documents({"status": "pending"})
    completed_count = tasks.count_documents({"status": "completed"})
    expired_count = tasks.count_documents({"status": "expired"})
    
    return {
        "pending": pending_count,
        "completed": completed_count,
        "expired": expired_count
    }

# =====================
# UI COMPONENTS
# =====================

# Login page
def login_page():
    st.title("Task Management System")
    st.subheader("Login")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        login_button = st.button("Login")
        
        if login_button:
            user = authenticate_user(username, password)
            if user:
                st.session_state.logged_in = True
                st.session_state.current_user = username
                st.session_state.role = user["role"]
                st.success(f"Login successful. Welcome, {username}!")
                st.rerun()
            else:
                st.error("Invalid username or password")
    
    with col2:
        st.markdown("""
        ### Demo Credentials
        
        **Admin:**
        - Username: admin
        - Password: admin123
        
        **Employees:**
        - Username: employee1
        - Password: employee1
        
        - Username: employee2
        - Password: employee2
        
        - Username: employee3
        - Password: employee3
        """)

# Admin dashboard
def admin_dashboard():
    st.title("Admin Dashboard")
    
    # Sidebar navigation
    st.sidebar.title(f"Welcome, {st.session_state.current_user}")
    admin_menu = st.sidebar.radio(
        "Admin Menu",
        ["Dashboard", "Task Management", "Employee Management", "Logout"]
    )
    
    if admin_menu == "Dashboard":
        admin_dashboard_view()
    elif admin_menu == "Task Management":
        admin_task_management()
    elif admin_menu == "Employee Management":
        admin_employee_management()
    elif admin_menu == "Logout":
        logout()

# Admin dashboard view
def admin_dashboard_view():
    st.header("System Overview")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.subheader("Task Distribution")
        
        # Get task distribution data
        task_distribution = get_task_distribution()
        
        # Create distribution chart
        fig = px.pie(
            names=["Pending", "Completed", "Expired"],
            values=[task_distribution["pending"], task_distribution["completed"], task_distribution["expired"]],
            color_discrete_sequence=["#2E86C1", "#58D68D", "#E74C3C"]
        )
        st.plotly_chart(fig)
    
    with col2:
        st.subheader("Employee Performance")
        
        # Get employee performance data
        performance_data = get_employee_performance()
        
        if performance_data:
            df = pd.DataFrame(performance_data)
            fig = px.bar(
                df,
                x="username",
                y="completion_rate",
                color="experience_level",
                title="Task Completion Rate (%)",
                labels={"username": "Employee", "completion_rate": "Completion Rate (%)"}
            )
            st.plotly_chart(fig)
    
    st.subheader("Employee Statistics")
    performance_data = get_employee_performance()
    if performance_data:
        df = pd.DataFrame(performance_data)
        st.dataframe(df)

# Admin task management
def admin_task_management():
    st.header("Task Management")
    
    tabs = st.tabs(["Create Tasks", "View Tasks"])
    
    with tabs[0]:
        st.subheader("Create New Task")
        
        with st.form(key="create_task_form"):
            task_title = st.text_input("Task Title")
            task_description = st.text_area("Task Description")
            
            col1, col2 = st.columns([1, 1])
            
            with col1:
                priority = st.selectbox("Priority", ["Low", "Medium", "High"])
            
            with col2:
                employees = get_all_employees()
                employee_usernames = [emp["username"] for emp in employees]
                assigned_to = st.selectbox("Assign To", employee_usernames)
            
            submit_button = st.form_submit_button("Create Task")
            
            if submit_button:
                if not task_title or not task_description:
                    st.error("Task title and description are required")
                else:
                    task = create_task(task_title, task_description, priority, assigned_to)
                    st.success(f"Task '{task_title}' created and assigned to {assigned_to}")
    
    with tabs[1]:
        st.subheader("View Tasks")
        
        # Status filter
        status_filter = st.selectbox("Filter by Status", ["All", "Pending", "Completed", "Expired"])
        
        # Get tasks based on filter
        tasks = get_all_tasks(status_filter)
        
        if not tasks:
            st.info("No tasks found matching the selected criteria")
        else:
            # Convert to DataFrame for display
            task_df = pd.DataFrame([
                {
                    "Task ID": task["task_id"],
                    "Title": task["title"],
                    "Priority": task.get("priority", "Medium"),
                    "Status": task["status"].capitalize(),
                    "Assigned To": task["assigned_to"],
                    "Created": task["created_at"].strftime("%Y-%m-%d %H:%M"),
                    "Deadline": task["deadline"].strftime("%Y-%m-%d %H:%M"),
                    "Time Left": str(timedelta(seconds=max(0, (task["deadline"] - datetime.now()).total_seconds()))) if task["status"] == "pending" else "N/A"
                }
                for task in tasks
            ])
            
            st.dataframe(task_df)
            
            # Task reassignment
            st.subheader("Task Reassignment")
            
            with st.form(key="reassign_task_form"):
                col1, col2 = st.columns([1, 1])
                
                with col1:
                    pending_tasks = [task for task in tasks if task["status"] == "pending"]
                    if pending_tasks:
                        task_options = {f"{task['title']} (assigned to {task['assigned_to']})": task["task_id"] for task in pending_tasks}
                        selected_task_display = st.selectbox("Select Task", list(task_options.keys()))
                        selected_task_id = task_options[selected_task_display]
                    else:
                        st.info("No pending tasks available for reassignment")
                        selected_task_id = None
                
                with col2:
                    employees = get_all_employees()
                    employee_usernames = [emp["username"] for emp in employees]
                    new_assignee = st.selectbox("New Assignee", employee_usernames)
                
                submit_button = st.form_submit_button("Reassign Task")
                
                if submit_button and selected_task_id:
                    db = get_database_connection()
                    tasks_collection = db["tasks"]
                    
                    # Update task assignment
                    tasks_collection.update_one(
                        {"task_id": selected_task_id},
                        {
                            "$set": {"assigned_to": new_assignee},
                            "$push": {"assignment_history": new_assignee}
                        }
                    )
                    
                    st.success(f"Task reassigned to {new_assignee}")

# Admin employee management
def admin_employee_management():
    st.header("Employee Management")
    
    tabs = st.tabs(["View Employees", "Add Employee", "Adjust Points"])
    
    with tabs[0]:
        st.subheader("Employee List")
        
        employees = get_all_employees()
        
        if not employees:
            st.info("No employees found")
        else:
            # Convert to DataFrame for display
            emp_df = pd.DataFrame([
                {
                    "Username": emp["username"],
                    "Experience Level": emp["experience_level"],
                    "Points": emp["points"],
                    "Created At": emp["created_at"].strftime("%Y-%m-%d %H:%M") if "created_at" in emp else "N/A"
                }
                for emp in employees
            ])
            
            st.dataframe(emp_df)
    
    with tabs[1]:
        st.subheader("Add New Employee")
        
        with st.form(key="add_employee_form"):
            new_username = st.text_input("Username")
            new_password = st.text_input("Password", type="password")
            
            submit_button = st.form_submit_button("Add Employee")
            
            if submit_button:
                if not new_username or not new_password:
                    st.error("Username and password are required")
                else:
                    db = get_database_connection()
                    users = db["users"]
                    
                    # Check if username already exists
                    existing_user = users.find_one({"username": new_username})
                    
                    if existing_user:
                        st.error(f"Username '{new_username}' already exists")
                    else:
                        # Create new employee
                        hashed_password = hash_password(new_password)
                        
                        users.insert_one({
                            "username": new_username,
                            "password": hashed_password,
                            "role": "employee",
                            "experience_level": "Junior",
                            "points": 100,
                            "task_history": [],
                            "created_at": datetime.now()
                        })
                        
                        st.success(f"Employee '{new_username}' added successfully")
    
    with tabs[2]:
        st.subheader("Adjust Employee Points")
        
        with st.form(key="adjust_points_form"):
            employees = get_all_employees()
            employee_usernames = [emp["username"] for emp in employees]
            
            selected_employee = st.selectbox("Select Employee", employee_usernames)
            point_adjustment = st.number_input("Point Adjustment", value=0, min_value=-50, max_value=50)
            
            submit_button = st.form_submit_button("Adjust Points")
            
            if submit_button:
                db = get_database_connection()
                users = db["users"]
                
                # Update points
                users.update_one(
                    {"username": selected_employee},
                    {"$inc": {"points": point_adjustment}}
                )
                
                if point_adjustment > 0:
                    st.success(f"Added {point_adjustment} points to {selected_employee}")
                elif point_adjustment < 0:
                    st.warning(f"Deducted {abs(point_adjustment)} points from {selected_employee}")
                else:
                    st.info("No points adjusted")

# Employee dashboard
def employee_dashboard():
    st.title("Employee Dashboard")
    
    # Sidebar navigation
    st.sidebar.title(f"Welcome, {st.session_state.current_user}")
    emp_menu = st.sidebar.radio(
        "Menu",
        ["My Tasks", "Performance", "Logout"]
    )
    
    if emp_menu == "My Tasks":
        employee_tasks_view()
    elif emp_menu == "Performance":
        employee_performance_view()
    elif emp_menu == "Logout":
        logout()

# Employee tasks view
def employee_tasks_view():
    st.header("My Tasks")
    
    # Get current user's tasks
    tasks = get_user_tasks(st.session_state.current_user)
    
    if not tasks:
        st.info("You have no pending tasks")
    else:
        for task in tasks:
            task_id = task["task_id"]
            
            # Calculate time remaining
            now = datetime.now()
            deadline = task["deadline"]
            time_remaining = max(0, (deadline - now).total_seconds())
            
            # Initialize timer in session state if not exists
            if task_id not in st.session_state.task_timers:
                st.session_state.task_timers[task_id] = time_remaining
            
            # Display task card
            with st.container():
                st.markdown("---")
                
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    st.subheader(task["title"])
                    st.write(f"**Priority:** {task.get('priority', 'Medium')}")
                    st.write(f"**Description:** {task['description']}")
                    
                    # Display creation and deadline
                    st.write(f"**Created:** {task['created_at'].strftime('%Y-%m-%d %H:%M')}")
                    st.write(f"**Deadline:** {deadline.strftime('%Y-%m-%d %H:%M')}")
                
                with col2:
                    # Display timer
                    minutes, seconds = divmod(int(time_remaining), 60)
                    st.markdown(f"### Time Left")
                    st.markdown(f"## {minutes:02d}:{seconds:02d}")
                    
                    # Complete task button
                    if st.button(f"Complete Task", key=f"complete_{task_id}"):
                        complete_task(task_id, st.session_state.current_user)
                        st.success("Task completed successfully!")
                        time.sleep(1)  # Brief pause
                        st.rerun()
            
            # Check for expired tasks
            if time_remaining <= 0 and task["status"] == "pending":
                handle_expired_task(task_id)
                st.warning(f"Task '{task['title']}' has expired and points have been deducted.")
                time.sleep(1)  # Brief pause
                st.rerun()

# Employee performance view
def employee_performance_view():
    st.header("My Performance")
    
    # Get user data
    db = get_database_connection()
    users = db["users"]
    tasks = db["tasks"]
    
    user = users.find_one({"username": st.session_state.current_user})
    
    if not user:
        st.error("User data not found")
        return
    
    # Display user info
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col1:
        st.metric("Experience Level", user["experience_level"])
    
    with col2:
        st.metric("Current Points", user["points"])
    
    with col3:
        # Calculate tasks completed
        completed_count = tasks.count_documents({
            "assignment_history": st.session_state.current_user,
            "status": "completed"
        })
        st.metric("Tasks Completed", completed_count)
    
    # Display task history
    st.subheader("Task History")
    
    # Get tasks where user is in assignment history
    user_task_history = list(tasks.find({
        "assignment_history": st.session_state.current_user
    }).sort("created_at", -1))
    
    if not user_task_history:
        st.info("No task history available")
    else:
        # Convert to DataFrame for display
        history_df = pd.DataFrame([
            {
                "Title": task["title"],
                "Status": task["status"].capitalize(),
                "Created At": task["created_at"].strftime("%Y-%m-%d %H:%M"),
                "Deadline": task["deadline"].strftime("%Y-%m-%d %H:%M"),
                "Current Assignee": task["assigned_to"]
            }
            for task in user_task_history
        ])
        
        st.dataframe(history_df)
    
    # Display experience points rules
    st.subheader("Experience Points System")
    
    st.markdown("""
    #### Level Progression
    - **Junior:** 0-20 completed tasks
    - **Mid:** 21-50 completed tasks
    - **Senior:** 51+ completed tasks
    
    #### Point Deductions for Expired Tasks
    - **Junior:** -5 points
    - **Mid:** -3 points
    - **Senior:** -2 points
    
    #### Point Rewards
    - **Task Completion:** +2 points
    """)

# Logout function
def logout():
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.role = None
    st.session_state.task_timers = {}
    st.success("Logged out successfully")
    time.sleep(1)
    st.rerun()

# =====================
# MAIN APPLICATION
# =====================

def main():
    # Set page config
    st.set_page_config(
        page_title="Task Management System",
        page_icon="✅",
        layout="wide"
    )
    
    # Initialize database
    initialize_database()
    
    # Initialize session state
    initialize_session_state()
    
    # Display appropriate page based on login status
    if not st.session_state.logged_in:
        login_page()
    else:
        if st.session_state.role == "admin":
            admin_dashboard()
        else:
            employee_dashboard()

if __name__ == "__main__":
    main()