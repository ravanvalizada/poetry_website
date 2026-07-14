document.addEventListener("DOMContentLoaded", function(){

    // DARK MODE
    const toggleDark = document.getElementById("toggle-dark");
    toggleDark.onclick = () => {
        const link = document.getElementById("dark-mode-css");
        link.disabled = !link.disabled;
    }

    // FOLLOW BUTTON
    const followBtn = document.getElementById("follow-btn");
    if(followBtn){
        followBtn.addEventListener("click", function(){
            fetch(`/toggle_follow/${followBtn.dataset.user}`, {method:"POST", headers:{"X-Requested-With":"XMLHttpRequest"}})
            .then(res=>res.json())
            .then(data=>{
                followBtn.textContent = data.action==="followed"?"Following":"Follow";
                document.querySelector('[data-type="followers"]').textContent = data.followers;
                document.querySelector('[data-type="following"]').textContent = data.following;
            });
        });
    }

    // FAVORITE BUTTONS
    document.querySelectorAll(".favorite-btn").forEach(btn=>{
        btn.addEventListener("click", function(){
            const poemId = this.dataset.poem;
            fetch(`/toggle_favorite/${poemId}`, {method:"POST", headers:{"X-Requested-With":"XMLHttpRequest"}})
            .then(res=>res.json())
            .then(data=>{
                this.textContent = data.action==="added"?"💖":"❤";
            });
        });
    });

    // SHARE BUTTONS
    document.querySelectorAll(".share-btn").forEach(btn=>{
        btn.addEventListener("click", ()=>{
            navigator.clipboard.writeText(btn.dataset.url);
            alert("Poem link copied!");
        });
    });

});
